# Reproducible builds

Use Python 3.12 and uv 0.11.32: `uv sync --locked --all-extras && uv build`. Regenerate `requirements.container.lock` only with the reviewed `uv export --locked --no-dev --no-emit-project --format requirements-txt` command. Build the pinned-base image with the full Git SHA as `GIT_REVISION`. CI emits the wheel/sdist, CycloneDX SBOM, lock digests, provenance, and image ID. Do not publish or promote.
