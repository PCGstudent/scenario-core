# syntax=docker/dockerfile:1.7
#
# Production quantitative worker image (architecture plan Section 22:
# docker/worker.Dockerfile, "digest-pinned base, non-root, threads pinned,
# MPLBACKEND=Agg"). Phase 2 built and smoke-tested this image locally and in
# CI only, with no AWS SDK/CLI/credential anywhere in it -- that was this
# file's own Phase 2 scope boundary. Phase 3b's `simulate --job-id` mode
# (worker/__main__.py) reads its job document from DynamoDB and its
# artifact from S3 through scenario_platform.adapters, which import boto3
# (the ONLY two files in the data plane allowed to, per pyproject.toml's
# Ruff exception) -- boto3/botocore are therefore now a real, deliberate
# part of this image (requirements/worker-image.lock), not a boundary
# violation of the Phase 2 comment this replaces. Still no AWS CLI and no
# credential baked into the image itself: the ECS task role supplies
# credentials at runtime via the container credential provider, never
# anything this Dockerfile embeds.

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
# botocore ships per-service API definitions for every AWS service AWS has
# ever published (432 directories, ~27 MB measured directly) under its own
# `data/` directory -- scenario_platform.adapters only ever constructs an
# S3 or a DynamoDB client/resource (job_store.py, s3_store.py; grepped, not
# assumed), so every other service's definition is dead weight this image
# has no reason to ship. Adding boto3/botocore for Phase 3b's `--job-id`
# mode pushed the built image from ~610 MB to 722 MB, over the Phase 2
# acceptance ceiling (<= 700 MB) that predates boto3 entirely.
#
# The prune MUST happen in this SAME `RUN` as the install, not a later one:
# Docker images are a stack of layers, and deleting a file in a LATER layer
# only adds a whiteout marker over it -- the deleted bytes are still
# physically present in the earlier `pip install` layer and still count
# toward the image's real size. A first attempt at this fix put the prune
# in its own subsequent `RUN` and measured NO size reduction at all
# (`docker images` still reported 722 MB) for exactly this reason; merging
# both into one layer is what actually removes the bytes.
RUN pip install --no-cache-dir --require-hashes -r /app/requirements/worker-image.lock \
    && BOTOCORE_DATA=$(python -c "import botocore, os; print(os.path.join(os.path.dirname(botocore.__file__), 'data'))") \
    && find "$BOTOCORE_DATA" -mindepth 1 -maxdepth 1 -type d ! -name s3 ! -name dynamodb -exec rm -rf {} + \
    && python -c "import boto3; boto3.client('s3', region_name='eu-west-1'); boto3.resource('dynamodb', region_name='eu-west-1'); print('s3/dynamodb client construction OK after data-directory pruning')"

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
