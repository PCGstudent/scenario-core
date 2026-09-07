"""Golden reproducibility check against the committed Phase-1 artifact fixture.

Architecture plan Section 16 (reproducibility contract) / Section 13
(committed fixture): ``tests/fixtures/gjr_skewt_v1/`` is a small (~72 KB)
committed ``ModelArtifact`` -- built once by ``scripts/build_artifact.py``
against the exact locally cached canonical dataset that also produced
``reports/run_manifest.json`` -- checked into the repository so this test
runs on any machine, in any CI environment, with no network access and no
local ``.cache/`` directory required. It exercises the Tier-1 bit-identity
claim (architecture plan Section 16.2) end to end, permanently: load the
fixture, verify its identity, simulate with a fixed request, and check both
the loaded ``artifact_id`` and the resulting scenario array's digest against
values recorded once and hardcoded below. If either ever changes, either the
domain layer's identity/simulation logic changed, or the fixture itself was
regenerated -- either way this test is designed to make that fact loud
rather than silently drift.

This is deliberately a *narrower* claim than machine-to-machine bit
reproducibility of a fresh calibration (Section 16.2's Tier 3, which this
repository does not claim): the fixture is generated once, on one machine,
and committed; every other machine only ever *loads* it, never refits it.
Loading, verifying, and simulating from a stored artifact is exactly what
Tier 1 promises, and this test is that promise's permanent regression check.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from scenario_platform.domain import serialization
from scenario_platform.domain.requests import ScenarioRequest
from scenario_platform.domain.services import simulate

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gjr_skewt_v1"

# Recorded once, from the committed fixture at tests/fixtures/gjr_skewt_v1/,
# built by:
#   python scripts/build_artifact.py --model-version gjr-skewt-20260907-1 \
#       --output tests/fixtures/gjr_skewt_v1 --cache-dir .cache
GOLDEN_ARTIFACT_ID = (
    "sha256:240a70185846dceae611110679961b7f50da865434d63b9664dc793de431bb51"
)
GOLDEN_MODEL_VERSION = "gjr-skewt-20260907-1"
# ScenarioRequest(model_version=GOLDEN_MODEL_VERSION, horizon=252, n_paths=1000, seed=42)
GOLDEN_SCENARIO_DIGEST = (
    "sha256:637920e584b8e82449a67b84bfc39b73528256aa6518d8ca8280087b9fb3c255"
)


def test_fixture_exists_and_is_small():
    assert (FIXTURE_DIR / serialization.METADATA_FILENAME).is_file()
    assert (FIXTURE_DIR / serialization.STATE_FILENAME).is_file()
    total_bytes = sum(f.stat().st_size for f in FIXTURE_DIR.iterdir() if f.is_file())
    # "~70KB scale" (architecture plan Section 13): a loose ceiling, not a
    # precise target -- this guards against ever accidentally committing a
    # much larger dataset-shaped file here, not against small day-to-day
    # variation in NPZ container overhead.
    assert total_bytes < 200_000, f"fixture unexpectedly large: {total_bytes} bytes"


def test_loading_the_committed_fixture_reproduces_the_golden_artifact_id():
    artifact = serialization.load_artifact(FIXTURE_DIR)  # fails closed on its own
    assert artifact.artifact_id == GOLDEN_ARTIFACT_ID
    assert artifact.model_version == GOLDEN_MODEL_VERSION


def test_simulating_from_the_committed_fixture_reproduces_the_golden_digest():
    artifact = serialization.load_artifact(FIXTURE_DIR)
    request = ScenarioRequest(
        model_version=artifact.model_version, horizon=252, n_paths=1000, seed=42
    )
    scenarios = simulate(artifact, request)
    digest = "sha256:" + hashlib.sha256(scenarios.returns.tobytes()).hexdigest()
    assert digest == GOLDEN_SCENARIO_DIGEST


def test_two_loads_of_the_committed_fixture_simulate_bit_identically():
    """The same claim as the digest check above, without hardcoding a hash --
    catches a bug the golden digest alone could theoretically miss if this
    process's own hashlib were somehow broken (it also serves as a
    self-contained regression check that needs no golden value at all)."""
    request = ScenarioRequest(
        model_version=GOLDEN_MODEL_VERSION, horizon=40, n_paths=15, seed=7
    )
    first = simulate(serialization.load_artifact(FIXTURE_DIR), request)
    second = simulate(serialization.load_artifact(FIXTURE_DIR), request)
    assert first.returns.tobytes() == second.returns.tobytes()
