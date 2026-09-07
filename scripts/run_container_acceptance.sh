#!/usr/bin/env bash
# Mandatory container acceptance for the Phase-2 worker image.
#
# Unlike tests/test_replay.py's container-gated tests (which SKIP cleanly
# when Docker is unavailable -- the correct behaviour for the ordinary host
# suite), this script is the opposite by design: it REQUIRES a working
# Docker daemon, builds the current source for linux/amd64, and FAILS if
# that daemon is unreachable, the build fails, or a single required
# container test is skipped. "The container suite passed" must never be
# confused with "the container suite was skipped."
#
# It builds against a specific, immutable local image ID (the digest
# `docker build` just produced), not a mutable tag: SCENARIO_WORKER_IMAGE is
# exported to that ID before pytest runs, so tests/test_replay.py's
# container tests -- which read that env var, falling back to the tag only
# when it is unset -- run against the exact image this script just built,
# never "whatever the tag happens to point to right now."
#
# Usage:
#   scripts/run_container_acceptance.sh
#
# Exit status: 0 only if the build succeeded, every required container test
# ran and passed, and zero of them were skipped.

set -euo pipefail

# python3 on PATH is a WSL/Linux-host assumption; this repo's Windows
# development environment only has "python" (python3 resolves via PATH to
# Windows's own App Execution Alias stub -- `command -v python3` reports it
# as present, since it genuinely is an executable on PATH, but running it
# prints a Microsoft Store prompt and exits nonzero instead of running any
# code; presence on PATH is therefore not sufficient, it must actually be
# invoked to tell the two apart) -- resolved once, here, rather than
# hard-coded at each call site below.
PY="python3"
"$PY" --version >/dev/null 2>&1 || PY="python"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Same host-path mismatch as the docker-vs-python split noted below,
# generalised: whenever $PY ends up being NATIVE Windows python.exe (no
# POSIX-path support), every path handed to it needs the "C:\..." form,
# not the MSYS "/c/..." form $REPO_ROOT is in (which docker.exe itself
# handles natively and does not need converting). PY_REPO_ROOT is that
# converted form when needed, and is exactly $REPO_ROOT unchanged
# everywhere cygpath does not exist (a genuine WSL/Linux host).
PY_REPO_ROOT="$REPO_ROOT"
command -v cygpath >/dev/null 2>&1 && PY_REPO_ROOT="$(cygpath -w "$REPO_ROOT")"

# On a genuine MSYS/Git-Bash host (as opposed to WSL, this script's other
# documented target), MSYS auto-rewrites ANY bare argument that looks like
# an absolute POSIX path when invoking a native Windows .exe -- not just
# `-v`/`--volume` values. That is fatal for this script's step 8, which
# passes container-INTERNAL paths (`--request /request.json`, etc.) as
# plain CLI arguments to `docker run`: MSYS rewrote `/request.json` into
# `C:/Program Files/Git/request.json` (confirmed directly), a path on the
# HOST that has nothing to do with the container's own filesystem. This
# script already does its own deliberate, explicit `-v` source-path
# translation (the wrapper installed below, via `wslpath -w`) for the one
# case that genuinely needs it; disabling MSYS's own blind, broader
# conversion is strictly more correct here, not a loss of functionality,
# and is a no-op (unset, ignored) on the WSL host this script primarily
# targets.
export MSYS_NO_PATHCONV=1
cd "$REPO_ROOT"

# --- 0. Locate a WORKING docker client ------------------------------------
# This repository's own WSL2 development distro has no native Docker Engine
# installed and is not registered in Docker Desktop's "WSL Integration"
# settings; the `docker` binary Docker Desktop still places on that
# distro's PATH (/mnt/c/.../resources/bin/docker, no .exe suffix) is a tiny
# stub that prints "enable WSL integration" guidance and exits 0 -- it must
# not be mistaken for a working client. The real client
# (.../resources/bin/docker.exe) works correctly when invoked directly, but
# a path containing spaces ("Program Files") is unreliable to pass through
# some shell invocation chains, so this block installs a one-line wrapper
# at a space-free path (~/bin/docker) the first time it is needed, and
# prefers that wrapper on every subsequent run. The wrapper also rewrites
# `-v`/`--volume` bind-mount SOURCE paths from WSL form ("/mnt/c/...") to
# Windows form ("C:\...") via `wslpath -w` before forwarding to docker.exe:
# invoked this way (outside Docker Desktop's own WSL Integration, which is
# not enabled for this distro), docker.exe does not resolve a WSL-style
# source path and silently mounts an empty directory in its place instead
# of the intended file -- confirmed directly.
DOCKER_BIN=""
WRAPPER="$HOME/bin/docker"
REAL_EXE="/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe"

if [ -x "$WRAPPER" ] && grep -q "wslpath" "$WRAPPER" 2>/dev/null; then
    DOCKER_BIN="$WRAPPER"
elif command -v docker >/dev/null 2>&1 && docker version 2>&1 | grep -q "^Server:"; then
    # A `docker` on PATH that actually talks to a daemon (Server section
    # present) -- e.g. a native Docker Engine, or a properly WSL-integrated
    # distro -- is used as-is, no wrapper needed (and no path translation:
    # a genuinely integrated `docker` handles that itself).
    DOCKER_BIN="docker"
elif [ -e "$REAL_EXE" ]; then
    mkdir -p "$HOME/bin"
    cat > "$WRAPPER" <<'WRAPPER_EOF'
#!/usr/bin/env bash
args=()
translate_next=0
for arg in "$@"; do
    if [ "$translate_next" = "1" ]; then
        source_path="${arg%%:*}"
        rest="${arg#*:}"
        if [[ "$source_path" == /* ]] && command -v wslpath >/dev/null 2>&1; then
            win_path="$(wslpath -w "$source_path" 2>/dev/null || true)"
            if [ -n "$win_path" ]; then
                arg="${win_path}:${rest}"
            fi
        fi
        args+=("$arg")
        translate_next=0
        continue
    fi
    case "$arg" in
        -v|--volume)
            args+=("$arg")
            translate_next=1
            ;;
        *)
            args+=("$arg")
            ;;
    esac
done
exec "/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe" "${args[@]}"
WRAPPER_EOF
    chmod +x "$WRAPPER"
    DOCKER_BIN="$WRAPPER"
fi

echo "=== 1. Docker daemon reachability (REQUIRED, not skipped) ==="
if [ -z "$DOCKER_BIN" ] || ! "$DOCKER_BIN" version >/dev/null 2>&1; then
    echo "FAIL: no reachable Docker daemon." >&2
    echo "This script requires a working Docker daemon -- it does not skip." >&2
    exit 1
fi
"$DOCKER_BIN" version
echo "docker client binary used: $DOCKER_BIN"
if [ "$DOCKER_BIN" = "$WRAPPER" ]; then
    echo "NOTE: this WSL distro is not registered in Docker Desktop's WSL"
    echo "Integration settings; the client above is the Windows Docker"
    echo "Desktop binary, invoked through a wrapper -- not a native Linux"
    echo "Docker Engine. The daemon it talks to (Server: below) is the"
    echo "same linux/amd64 engine either way."
fi

echo
echo "=== 2. Build for linux/amd64 from current source ==="
GIT_SHA="$(git rev-parse HEAD)"
BUILD_TAG="scenario-core-worker:acceptance-${GIT_SHA:0:12}"
"$DOCKER_BIN" build \
    --platform linux/amd64 \
    --provenance=false \
    --sbom=false \
    --build-arg "WORKER_GIT_SHA=${GIT_SHA}" \
    --build-arg "WORKER_IMAGE_REF=${BUILD_TAG}" \
    -f docker/worker.Dockerfile \
    -t "$BUILD_TAG" \
    .

IMAGE_ID="$("$DOCKER_BIN" image inspect "$BUILD_TAG" --format '{{.Id}}')"
if [ -z "$IMAGE_ID" ]; then
    echo "FAIL: could not resolve an image ID for $BUILD_TAG" >&2
    exit 1
fi
echo "built image ID (immutable): $IMAGE_ID"
echo "built image tag (mutable, informational only): $BUILD_TAG"

PLATFORM="$("$DOCKER_BIN" inspect "$IMAGE_ID" --format '{{.Os}}/{{.Architecture}}')"
echo "reported platform: $PLATFORM"
if [ "$PLATFORM" != "linux/amd64" ]; then
    echo "FAIL: expected linux/amd64, got $PLATFORM" >&2
    exit 1
fi

echo
echo "=== 3. Image size (docker images' column -- see note) ==="
# docker image inspect --format {{.Size}} under-reports on this Docker
# Desktop version (containerd image store): confirmed directly against the
# unmodified upstream python:3.13-slim base, where `docker images` reports
# 189 MB but `docker inspect --format {{.Size}}` reports ~46 MB for the
# same image. `docker images`' column is what a human checking "how big is
# this image" actually sees, so it is the one measured and reported here.
SIZE_HUMAN="$("$DOCKER_BIN" images "$BUILD_TAG" --format '{{.Size}}')"
echo "image size (measurement convention: \`docker images\` Size column, NOT \`docker inspect --format {{.Size}}\` -- the latter under-reports on this Docker Desktop version): $SIZE_HUMAN"

echo
echo "=== 4. Non-root UID ==="
UID_REPORTED="$("$DOCKER_BIN" run --rm --platform linux/amd64 --entrypoint id "$IMAGE_ID" -u)"
echo "runtime UID: $UID_REPORTED"
if [ "$UID_REPORTED" = "0" ]; then
    echo "FAIL: image runs as root" >&2
    exit 1
fi

echo
echo "=== 5. Prohibited packages ==="
PIP_FREEZE="$("$DOCKER_BIN" run --rm --platform linux/amd64 --entrypoint python "$IMAGE_ID" -m pip list --format freeze)"
echo "$PIP_FREEZE"
for PKG in streamlit plotly pyarrow; do
    if echo "$PIP_FREEZE" | grep -qi "^${PKG}"; then
        echo "FAIL: prohibited package '$PKG' is installed" >&2
        exit 1
    fi
done
echo "confirmed absent: streamlit, plotly, pyarrow"

echo
echo "=== 6. Actual runtime thread counts (threadpoolctl, not env vars) ==="
THREAD_POOLS="$("$DOCKER_BIN" run --rm --platform linux/amd64 --entrypoint python "$IMAGE_ID" -c \
    "import numpy, scipy, threadpoolctl, json; print(json.dumps(threadpoolctl.threadpool_info()))")"
echo "$THREAD_POOLS"
if echo "$THREAD_POOLS" | "$PY" -c "import json,sys; pools=json.load(sys.stdin); sys.exit(0 if all(p['num_threads']==1 for p in pools) and pools else 1)"; then
    echo "confirmed: every loaded thread pool reports num_threads=1"
else
    echo "FAIL: at least one thread pool did not report num_threads=1" >&2
    exit 1
fi

echo
echo "=== 7. Cold-import time ==="
COLD_IMPORT_OUTPUT="$("$DOCKER_BIN" run --rm --platform linux/amd64 --entrypoint python "$IMAGE_ID" -c \
    "import time; t0=time.monotonic(); import scenario_platform.worker.__main__; print(f'cold_import_seconds={time.monotonic()-t0:.3f}')")"
echo "$COLD_IMPORT_OUTPUT"

echo
echo "=== 8. Golden replay (single run, exact digest check) ==="
# A repo-relative scratch dir (not load-bearing for correctness -- the
# docker wrapper installed in step 0 rewrites any `-v` SOURCE path to
# Windows form via `wslpath -w` regardless of where it lives, including a
# WSL-native /tmp path; kept repo-relative here only so a failed run's
# leftovers are easy to find next to the repo instead of in /tmp).
ACCEPT_DIR="$REPO_ROOT/.acceptance-scratch"
rm -rf "$ACCEPT_DIR"
mkdir -p "$ACCEPT_DIR"
trap 'rm -rf "$ACCEPT_DIR"' EXIT
cat > "$ACCEPT_DIR/request.json" <<'EOF'
{
  "artifact_id": "sha256:fdf0b19144e0e899f29eb88aad30af4bb248deb7986ce34879d6cd00be29913f",
  "model_version": "gjr-skewt-20260907-1",
  "horizon": 252,
  "n_paths": 1000,
  "seed": 42
}
EOF
mkdir -p "$ACCEPT_DIR/output"
"$DOCKER_BIN" run --rm --platform linux/amd64 \
    -v "$REPO_ROOT/tests/fixtures/gjr_skewt_v1:/artifact:ro" \
    -v "$ACCEPT_DIR/request.json:/request.json:ro" \
    -v "$ACCEPT_DIR/output:/output" \
    "$IMAGE_ID" simulate --artifact-dir /artifact --request /request.json --output-dir /output
# The container itself (via docker.exe's own POSIX-path handling for -v,
# unrelated to MSYS_NO_PATHCONV above) reads/writes this path just fine --
# but $PY, when it resolves to native Windows python.exe (this host), has
# no POSIX-path support of its own and needs the real "C:\..." form for
# the SAME file to open it afterward. cygpath (ships with Git for Windows)
# does that conversion; on a genuine WSL/Linux host, where the original
# path is already correct and cygpath does not exist, this falls back to
# it unchanged.
RETURNS_PATH="$ACCEPT_DIR/output/returns.npy"
command -v cygpath >/dev/null 2>&1 && RETURNS_PATH="$(cygpath -w "$RETURNS_PATH")"
ARRAY_DIGEST="$("$PY" -c "
import hashlib, numpy as np
a = np.load(r'$RETURNS_PATH')
print('sha256:' + hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest())
")"
echo "golden array digest obtained: $ARRAY_DIGEST"
EXPECTED="sha256:637920e584b8e82449a67b84bfc39b73528256aa6518d8ca8280087b9fb3c255"
if [ "$ARRAY_DIGEST" != "$EXPECTED" ]; then
    echo "FAIL: digest mismatch. expected $EXPECTED, got $ARRAY_DIGEST" >&2
    exit 1
fi
echo "MATCHES the Phase-1 golden array digest exactly."

echo
echo "=== 9. Full container test suite against this exact image ID, zero skips allowed ==="
# Relies on the caller's already-active Python environment (this repository's
# established practice: `source .../phase1-venv/bin/activate` before running
# any check) -- not auto-detected, so this script never silently picks up
# the wrong interpreter.
export SCENARIO_WORKER_IMAGE="$IMAGE_ID"

# pytest's own tmp_path fixture defaults to a WSL-native /tmp path. The
# docker wrapper (step 0) rewrites `-v` SOURCE paths via `wslpath -w`, which
# turns a WSL-native path into a "\\wsl$\..." UNC path -- and Docker Desktop
# can only route that through the per-distro WSL-integration guest-services
# socket, which does not exist for a distro that is not registered in
# Integration settings (confirmed directly: "accessing specified distro
# mount service: ... no such file or directory"). Pinning --basetemp under
# the repository root keeps every tmp_path on the Windows-visible /mnt/c/...
# filesystem, so translation produces a plain "C:\..." path instead.
PYTEST_BASETEMP="$PY_REPO_ROOT/.acceptance-pytest-tmp"
rm -rf "$PYTEST_BASETEMP"
mkdir -p "$PYTEST_BASETEMP"

REPORT_LOG="$(mktemp)"
set +e
"$PY" -m pytest "$PY_REPO_ROOT/tests/test_replay.py" -k "container or image_" \
    --basetemp="$PYTEST_BASETEMP" -o addopts="" -v 2>&1 | tee "$REPORT_LOG"
# NOT `$?` here -- that would capture `tee`'s exit status, not pytest's,
# since this is the last command in a pipeline. PIPESTATUS[0] is pytest's.
PYTEST_STATUS="${PIPESTATUS[0]}"
set -e

SKIPPED_COUNT="$(grep -oE '[0-9]+ skipped' "$REPORT_LOG" | grep -oE '[0-9]+' || echo 0)"
rm -f "$REPORT_LOG"
rm -rf "$PYTEST_BASETEMP"

if [ "$PYTEST_STATUS" -ne 0 ]; then
    echo "FAIL: container pytest lane did not pass (exit $PYTEST_STATUS)" >&2
    exit 1
fi
if [ "${SKIPPED_COUNT:-0}" -gt 0 ]; then
    echo "FAIL: $SKIPPED_COUNT container test(s) were skipped -- with a reachable Docker" \
         "daemon and a just-built image, zero skips are acceptable here." >&2
    exit 1
fi
echo "confirmed: 0 container tests skipped."

echo
echo "=================================================================="
echo "ACCEPTANCE PASSED"
echo "  image ID:        $IMAGE_ID"
echo "  platform:         $PLATFORM"
echo "  size:             $SIZE_HUMAN (docker images Size column)"
echo "  runtime UID:      $UID_REPORTED"
echo "  $COLD_IMPORT_OUTPUT"
echo "  golden digest:    $ARRAY_DIGEST"
echo "=================================================================="
