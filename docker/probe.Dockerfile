# Phase 3a's one-off connectivity-probe image (IMPLEMENTATION_PLAN.md
# Section 24 Phase 3a acceptance criterion 3). Deliberately separate from
# docker/worker.Dockerfile: this never ships application code, never
# handles a scenario request, and is not part of the build-once/promote-
# by-digest pipeline (Section 18.2) -- it is infrastructure-acceptance
# tooling, pushed once to the same ECR repository under its own tag and
# run as a single ECS RunTask, not deployed as a service.

FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# boto3 only -- no scientific stack, no application code. Not hash-pinned
# via a committed lock file the way worker-image.lock is: this image is
# never the subject of a reproducibility claim (Section 16), it only
# needs to run once per acceptance cycle, so a plain version pin is
# proportionate.
RUN pip install --no-cache-dir "boto3==1.40.30"

COPY scripts/connectivity_probe.py /app/connectivity_probe.py

RUN groupadd --gid 65532 probe \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin probe
USER 65532:65532

ENTRYPOINT ["python", "/app/connectivity_probe.py"]
