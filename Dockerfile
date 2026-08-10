# Watch Clank -- Linux AMD64 staging image. Under construction / not yet
# production. Pipeline (one-shot) and migrate (explicit, deliberate) share
# this image; the dashboard is NOT deployed by this image in this phase
# (see ai/handoff/SQLITE_COORDINATION.md -- it is read-only and safe to add
# later, but adds a second service for zero soak-correctness benefit today).
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin watchclank

WORKDIR /app

# app/ must exist before `pip install .` (hatchling needs the package
# source present to resolve) -- the .draft version copied pyproject.toml
# alone first, which it flagged as illustrative-only, not a working step.
COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts

RUN pip install --no-cache-dir .

# Full Git SHA this image was built from. Must be passed at build time.
# Never derived from a .git directory at runtime -- none is copied into
# this image. Pattern proven on OEM Radar / Chinese Tech Wire / Feature
# Phone Clank / Smartwatch Clank.
ARG GIT_REVISION=unknown
LABEL clank.id="watch-clank" \
      org.opencontainers.image.revision="${GIT_REVISION}"
ENV WATCH_CLANK_SOURCE_REVISION=${GIT_REVISION}

# Authoritative state (data/watch_clank.db, WAL-mode SQLite, and
# data/snapshots/, content-addressed evidence blobs) lives on a mounted
# volume, not the container filesystem.
RUN mkdir -p /data && chown -R watchclank:watchclank /data /app

USER watchclank

ENTRYPOINT ["/usr/bin/tini", "--"]

# Default CMD is the one-shot pipeline job. The `migrate` command (see
# scripts/migrate.py) is invoked explicitly and separately by an operator/
# deploy script before switching a deployment to a new schema version --
# never run automatically by this image's default CMD.
CMD ["python", "-m", "scripts.run_pipeline", "--scheduled"]
