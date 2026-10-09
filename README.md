# Public Harbor 0.24.0 sidecar build package

This is a preparation-only package. No image has been built or imported, and
no external workflow has been run as part of preparation.

The five files in context/ are copied byte-for-byte from the official Harbor
0.24.0 wheel. Their public wheel URL, SHA256, sizes, original permissions,
platform, pinned GOST base digest, context key, and intended tag are recorded in
expected.json. MANIFEST.sha256 covers all five files, including the empty file.
No custom Dockerfile, task assets, registry credentials, or scoring code is used.

After separate authorization, publish ONLY the contents of this package as the
root of a dedicated public repository, including .github/ and the empty
context/allowlist.txt. Do not publish its parent directory. The workflow file
must be on the repository default branch for workflow_dispatch to be available.
It has no push, pull-request, or schedule trigger. Starting it is a separate
manual action, not part of package preparation.

The future workflow needs GitHub-hosted ubuntu-22.04 x86_64 Linux with Docker and
Buildx available, and outbound access to public PyPI and Docker Hub. It uses
read-only repository permissions and persists no checkout credentials.
Git normalizes permissions; the verification helper first checks file bytes,
then restores the wheel-context permissions without changing content and verifies
Harbor's mode-sensitive context key. The context key is a Harbor cache identifier,
not a cryptographic image identity.

The producer rechecks the official wheel and pinned raw registry manifest, saves
complete commands, stdout, stderr, and actual subprocess exit codes, builds using
docker buildx build --platform linux/amd64 --load, inspects the result, checks
platform, sole expected tag, and config image ID, and uses docker save.
A failing producer makes the job fail while the always-run artifact step retains
its available failure logs. BuildKit's metadata JSON retains full provenance;
this does not claim that the Docker image store preserves attestations.

Only a SUCCESS build-receipt.json together with matching inspect, metadata,
archive SHA256, byte count, and image ID is a candidate for later import review.
An uploaded artifact alone is not evidence of a successful or valid image.
Image transfer, target import, and runtime validation remain separate stages.

Public references:
- Harbor wheel: see expected.json.
- Buildx options: https://docs.docker.com/reference/cli/docker/buildx/build/
- Raw manifest byte preservation: https://github.com/docker/buildx/blob/v0.30.1/util/imagetools/printers.go
- Manual dispatch: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- Artifact retention: https://docs.github.com/en/actions/tutorials/store-and-share-data
