#!/usr/bin/env python3
"""Future GitHub Actions producer. Never executed by static package preparation."""
import datetime
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from verify_context import verify

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = [
    "expected.json", "MANIFEST.sha256", "README.md", "static_audit.json",
    "scripts/verify_context.py", "scripts/build_sidecar.py",
    "context/Dockerfile", "context/allowlist.txt", "context/gost.yaml",
    "context/entrypoint.sh", "context/bin/network-policy",
]
WHEEL_PREFIX = "harbor/environments/docker/harbor-docker-egress-control-sidecar/"


class ProducerFailure(Exception):
    def __init__(self, step, returncode):
        super().__init__(step + " failed with producer returncode " + str(returncode))
        self.returncode = returncode


def write_json(path, record):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_command(label, argv, output, receipt):
    stdout = output / (label + ".stdout.log")
    stderr = output / (label + ".stderr.log")
    command_record = {"step": label, "argv": argv, "exit_code": None}
    receipt["commands"].append(command_record)
    with stdout.open("xb") as out_handle, stderr.open("xb") as err_handle:
        result = subprocess.run(argv, cwd=ROOT, stdout=out_handle,
                                stderr=err_handle, check=False)
    command_record["exit_code"] = result.returncode
    (output / (label + ".exit_code.txt")).write_text(
        str(result.returncode) + "\n", encoding="utf-8"
    )
    print(json.dumps(command_record), flush=True)
    if result.returncode != 0:
        raise ProducerFailure(label, result.returncode)
    return stdout


def verify_official_wheel(expected, output):
    request = urllib.request.Request(
        expected["harbor_wheel_url"], headers={"User-Agent": "public-sidecar-build"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read(5 * 1024 * 1024 + 1)
    if len(content) != expected["harbor_wheel_bytes"]:
        raise ValueError("Official wheel size mismatch")
    digest = hashlib.sha256(content).hexdigest()
    if digest != expected["harbor_wheel_sha256"]:
        raise ValueError("Official wheel SHA256 mismatch")
    required = {item["path"] for item in expected["context_files"]}
    with zipfile.ZipFile(io.BytesIO(content)) as wheel:
        actual = {
            name.removeprefix(WHEEL_PREFIX) for name in wheel.namelist()
            if name.startswith(WHEEL_PREFIX) and not name.endswith("/")
        }
        if actual != required:
            raise ValueError("Official wheel context file set mismatch")
        for item in expected["context_files"]:
            if wheel.read(WHEEL_PREFIX + item["path"]) != (
                ROOT / "context" / item["path"]
            ).read_bytes():
                raise ValueError("Build context differs from official wheel")
    result = {"url": expected["harbor_wheel_url"], "sha256": digest,
              "bytes": len(content), "all_five_files_byte_identical": True}
    write_json(output / "official-wheel-verification.json", result)
    return result


def main():
    os.chdir(ROOT)
    output = ROOT / "provenance"
    output.mkdir(exist_ok=True)
    # Reject stale producer assets rather than overwriting them.
    allowed_existing = {"checksums.stdout.log", "checksums.stderr.log",
                        "checksums.exit_code.txt"}
    if {p.name for p in output.iterdir()} - allowed_existing:
        raise ValueError("Provenance directory contains stale producer output")
    expected = json.loads((ROOT / "expected.json").read_text())
    receipt = {
        "status": "FAILED",
        "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "harbor_version": expected["harbor_version"],
        "harbor_wheel_sha256": expected["harbor_wheel_sha256"],
        "dockerfile_sha256": next(
            item["sha256"] for item in expected["context_files"]
            if item["path"] == "Dockerfile"
        ),
        "context_files": expected["context_files"],
        "context_key": expected["context_key"],
        "base_image": expected["base_image"],
        "platform": expected["platform"],
        "target_tag": expected["target_tag"],
        "repository_commit_sha": os.environ.get("GITHUB_SHA"),
        "workflow_file_sha256": sha256_file(ROOT / ".github/workflows/build-sidecar.yml"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_run_url": (
            os.environ.get("GITHUB_SERVER_URL", "https://github.com") + "/" +
            os.environ.get("GITHUB_REPOSITORY", "") + "/actions/runs/" +
            os.environ.get("GITHUB_RUN_ID", "")
        ),
        "runner": {
            "os": os.environ.get("RUNNER_OS"),
            "arch": os.environ.get("RUNNER_ARCH"),
            "platform_machine": platform.machine(),
            "image_os": os.environ.get("ImageOS"),
            "image_version": os.environ.get("ImageVersion"),
        },
        "commands": [],
        "source_image_id": None,
        "image_archive_sha256": None,
        "image_archive_bytes": None,
        "github": {name: os.environ.get(name) for name in [
            "GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID",
            "GITHUB_RUN_ATTEMPT", "GITHUB_SERVER_URL",
        ]},
    }
    exit_code = 1
    try:
        if sys.platform != "linux" or platform.machine() not in ("x86_64", "amd64"):
            raise ValueError("Producer must be x86_64 Linux")
        receipt["context_verification"] = verify(ROOT, restore_modes=True)
        source = output / "source"
        for name in PUBLIC_FILES:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        # A non-hidden filename ensures this provenance is included in artifacts.
        shutil.copy2(ROOT / ".github/workflows/build-sidecar.yml",
                     source / "workflow.yml")
        receipt["official_wheel_verification"] = verify_official_wheel(expected, output)
        run_command("runner-uname", ["uname", "-a"], output, receipt)
        run_command("runner-os-release", ["cat", "/etc/os-release"], output, receipt)
        run_command("docker-version", ["docker", "version"], output, receipt)
        run_command("buildx-version", ["docker", "buildx", "version"], output, receipt)
        raw = run_command("base-manifest", [
            "docker", "buildx", "imagetools", "inspect", "--raw",
            expected["base_image"],
        ], output, receipt)
        base_digest = "sha256:" + sha256_file(raw)
        if base_digest != expected["base_image_digest"]:
            raise ValueError("Raw registry manifest digest differs from pinned base")
        receipt["verified_base_manifest_digest"] = base_digest
        os.environ["BUILDX_METADATA_PROVENANCE"] = "max"
        build_command = [
            "docker", "buildx", "build", "--platform", "linux/amd64", "--load",
            "--pull", "--progress=plain",
            "--metadata-file", "provenance/build-metadata.json",
            "--file", "context/Dockerfile",
            "-t", expected["target_tag"], "./context",
        ]
        receipt["build_command"] = build_command
        run_command("build", build_command, output, receipt)
        inspect_file = run_command("image-inspect", [
            "docker", "image", "inspect", expected["target_tag"],
        ], output, receipt)
        images = json.loads(inspect_file.read_text())
        if len(images) != 1:
            raise ValueError("Expected exactly one inspected image")
        image = images[0]
        if (image.get("Os"), image.get("Architecture")) != ("linux", "amd64"):
            raise ValueError("Built image platform mismatch")
        if image.get("RepoTags") != [expected["target_tag"]]:
            raise ValueError("Unexpected image tags")
        image_id = image["Id"]
        if not image_id.startswith("sha256:") or len(image_id) != 71:
            raise ValueError("Invalid image ID")
        metadata = json.loads((output / "build-metadata.json").read_text())
        if metadata.get("containerimage.config.digest") != image_id:
            raise ValueError("Build metadata and image inspect ID differ")
        if not metadata.get("buildx.build.provenance"):
            raise ValueError("Build provenance metadata is missing")
        receipt["source_image_id"] = image_id
        receipt["image_created_at"] = image["Created"]
        receipt["image_os"] = image["Os"]
        receipt["image_architecture"] = image["Architecture"]
        receipt["platform_verified"] = True
        receipt["only_expected_tag_verified"] = True
        archive = output / "harbor-sidecar.tar"
        run_command("save", ["docker", "save", "--output", str(archive),
                             expected["target_tag"]], output, receipt)
        if archive.stat().st_size == 0:
            raise ValueError("Empty Docker image archive")
        receipt["image_archive_sha256"] = sha256_file(archive)
        receipt["image_archive_bytes"] = archive.stat().st_size
        receipt["image_archive_file"] = archive.name
        (output / "image-ID.txt").write_text(image_id + "\n")
        (output / "image-archive-bytes.txt").write_text(str(archive.stat().st_size) + "\n")
        (output / "image-archive.sha256").write_text(
            receipt["image_archive_sha256"] + "  " + archive.name + "\n"
        )
        receipt["status"] = "SUCCESS"
        exit_code = 0
    except ProducerFailure as error:
        receipt["failure_reason"] = str(error)
        # Preserve raw producer status in its log; map a signal to shell status.
        exit_code = error.returncode if error.returncode > 0 else 128 - error.returncode
    except Exception as error:
        receipt["failure_reason"] = type(error).__name__ + ": " + str(error)
        exit_code = 1
    finally:
        receipt["completed_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        receipt["workflow_producer_exit_code"] = exit_code
        write_json(output / "build-receipt.json", receipt)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
