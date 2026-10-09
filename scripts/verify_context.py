#!/usr/bin/env python3
"""Verify the public wheel context; optionally restore Git-normalized modes."""
import argparse
import hashlib
import json
import os
import stat
from pathlib import Path


def context_key(context, platform):
    entries = []
    for current, dirs, files in os.walk(context, topdown=True, followlinks=False):
        dirs.sort()
        entries.extend(Path(current) / name for name in sorted(files))
    digest = hashlib.blake2b(digest_size=8)
    dockerfile = context / "Dockerfile"
    digest.update(dockerfile.name.encode())
    digest.update(dockerfile.read_bytes())
    for path in entries:
        info = path.lstat()
        digest.update(str(path.relative_to(context)).encode())
        digest.update(info.st_mode.to_bytes(4, "little"))
        digest.update(info.st_size.to_bytes(8, "little"))
        if stat.S_ISLNK(info.st_mode):
            digest.update(os.readlink(path).encode())
        elif stat.S_ISREG(info.st_mode):
            digest.update(path.read_bytes())
    digest.update(b"platform\0")
    digest.update(platform.encode())
    digest.update(b"\0")
    digest.update(b"build_args\0")  # Harbor uses an empty build-argument mapping.
    return digest.hexdigest()


def verify(root, restore_modes=False):
    root = Path(root)
    expected = json.loads((root / "expected.json").read_text())
    context = root / "context"
    records = expected["context_files"]
    required = {item["path"] for item in records}
    actual = set()
    for path in context.rglob("*"):
        if path.is_symlink():
            raise ValueError("Context symlink is forbidden")
        if path.is_file():
            actual.add(path.relative_to(context).as_posix())
        elif not path.is_dir():
            raise ValueError("Context contains a non-regular entry")
    if actual != required:
        raise ValueError("Context file set differs from the five wheel files")
    for item in records:
        path = context / item["path"]
        content = path.read_bytes()
        if len(content) != item["bytes"]:
            raise ValueError("Context file size mismatch: " + item["path"])
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("Context SHA256 mismatch: " + item["path"])
    # Git retains executable bits but not group-write bits. Change metadata only.
    for item in records:
        path = context / item["path"]
        mode = int(item["mode"], 8)
        if restore_modes:
            path.chmod(mode)
        if stat.S_IMODE(path.stat().st_mode) != mode:
            raise ValueError("Context permissions mismatch: " + item["path"])
    computed = context_key(context, expected["platform"])
    if computed != expected["context_key"]:
        raise ValueError("Harbor context key mismatch")
    first_line = (context / "Dockerfile").read_text().splitlines()[0]
    unqualified = expected["base_image"].removeprefix("docker.io/")
    if first_line != "FROM " + unqualified:
        raise ValueError("Pinned Dockerfile FROM mismatch")
    return {
        "content_sha256_verified": True,
        "original_modes_verified": True,
        "context_key": computed,
        "platform": expected["platform"],
        "file_count": len(records),
        "file_contents_modified": False,
        "permissions_restored": restore_modes,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore-modes", action="store_true")
    options = parser.parse_args()
    result = verify(Path(__file__).resolve().parents[1], options.restore_modes)
    print(json.dumps(result, indent=2))
