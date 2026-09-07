# syntax=docker/dockerfile:1.7
#
# Production quantitative worker image (architecture plan Section 22:
# docker/worker.Dockerfile, "digest-pinned base, non-root, threads pinned,
# MPLBACKEND=Agg"). Phase 2 builds and smoke-tests this image locally and in
# CI only -- nothing here pushes to a registry, launches a task, or touches
# AWS in any way; there is no AWS SDK, no AWS CLI, and no credential of any
# kind anywhere in this file (Phase 2's own scope boundary).

FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

# Populated by CI at build time (git rev-parse HEAD; the resulting image
# reference) so the worker can record them in its manifest.json without
# needing `.git` or any build metadata inside the runtime image itself.
# Empty by default -- absence is recorded as absence (null), never guessed.
ARG WORKER_GIT_SHA=""
ARG WORKER_IMAGE_REF=""

# Deterministic numerical runtime (architecture plan Section 8: "OMP_NUM_THREADS=1,
# OPENBLAS_NUM_THREADS=1, MKL_NUM_THREADS=1, PYTHONHASHSEED=0"). Every BLAS
# implementation NumPy/SciPy might resolve to is pinned to one thread before
# Python -- and therefore before any BLAS library -- ever starts, so
# `simulate`'s reproducibility does not depend on which one got linked at
# runtime. MPLBACKEND=Agg matches the quantitative core's own invariant 22
# (figures are written, never displayed) in case anything transitively
# touches matplotlib; PYTHONHASHSEED=0 removes hash-randomisation as a
# source of any non-determinism in code that happens to iterate a dict/set.
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    PYTHONHASHSEED=0 \
    MPLBACKEND=Agg \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    WORKER_GIT_SHA=${WORKER_GIT_SHA} \
    WORKER_IMAGE_REF=${WORKER_IMAGE_REF}

WORKDIR /app

# Locked, hash-pinned scientific runtime only -- no dev/UI extras.
# requirements/worker-image.lock is a strict subset of requirements/worker.lock
# (same pins, never a different resolution): it excludes yfinance and
# matplotlib, which requirements/worker-image.in's own header comment
# documents as genuinely unreachable from the worker's own import graph
# (verified by grep across every xtra_takehome module the worker imports),
# on top of the streamlit/plotly/pyarrow exclusion worker.lock already
# makes. --require-hashes makes an unpinned or substituted transitive
# dependency a hard build failure, not a silent drift.
COPY requirements/worker-image.lock /app/requirements/worker-image.lock
RUN pip install --no-cache-dir --require-hashes -r /app/requirements/worker-image.lock

# Only the two source trees the worker actually imports: the quantitative
# core (src/xtra_takehome, unchanged, invariant-bearing) and the platform
# layer (domain + worker + adapters). xtra_takehome/app/ (the Streamlit lab)
# and every other unrelated path are excluded via .dockerignore, not by a
# narrower COPY here -- the .dockerignore is the single source of truth for
# what belongs in the build context at all.
COPY src/ /app/src/

# Non-root at runtime: a fixed, low, non-privileged UID/GID defined by this
# Dockerfile rather than whatever UID the base image happens to ship this
# week, so "who the worker runs as" is a property of this file, not an
# accident of the base image's own build.
RUN groupadd --gid 65532 worker \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin worker
USER 65532:65532

ENTRYPOINT ["python", "-m", "scenario_platform.worker"]
