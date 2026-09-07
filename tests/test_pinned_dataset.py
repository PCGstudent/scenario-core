"""Proves the "pinned dataset" claim is literally true, not aspirational.

``tests/fixtures/brent_dataset_v1/closes.csv`` is a committed, byte-for-byte
copy of the exact locally cached CSV (previously ``.cache/BZF_2010-01-01_2026-09-01.csv``,
gitignored and machine-local) that produced both ``reports/run_manifest.json``
and the committed artifact fixture at ``tests/fixtures/gjr_skewt_v1/``. Copying
it into the repository -- rather than continuing to read it from a gitignored
cache -- is what makes "pinned dataset" true of the ordinary test suite: any
clean clone has the same 4,159 dated closes any other clone has, with no
network access and no dependence on a machine-local file outside the repo.

This file is the standalone proof of that; ``tests/scenario_platform/test_domain.py``
consumes the same fixture for the validation-reproduction tests without
re-deriving these facts itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scenario_platform.domain.artifacts import build_dataset_ref
from xtra_takehome.data import _validate_close, log_returns_pct

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "brent_dataset_v1" / "closes.csv"
GOLDEN_ARTIFACT_JSON = Path(__file__).parent / "fixtures" / "gjr_skewt_v1" / "artifact.json"


def _load_fixture_close_series() -> pd.Series:
    raw = pd.read_csv(FIXTURE_CSV, index_col=0, parse_dates=True).iloc[:, 0]
    return _validate_close(raw.astype(float).rename("close"))


def test_fixture_file_is_committed_and_small():
    assert FIXTURE_CSV.is_file()
    size = FIXTURE_CSV.stat().st_size
    # ~120KB: the actual 4,159-row CSV, not a placeholder and not a larger
    # dump of some other dataset.
    assert 50_000 < size < 250_000, f"unexpected fixture size: {size} bytes"


def test_fixture_has_exactly_4159_closes():
    close = _load_fixture_close_series()
    assert len(close) == 4159


def test_fixture_derives_exactly_4158_returns():
    close = _load_fixture_close_series()
    returns = log_returns_pct(close)
    assert len(returns) == 4158


def test_fixture_window_matches_the_pinned_ticker_and_dates():
    close = _load_fixture_close_series()
    assert str(close.index[0].date()) == "2010-01-04"  # first trading day on/after start
    # Last trading day before end (exclusive):
    assert str(close.index[-1].date()) == "2026-08-31"
    # The pinned window itself, as xtra_takehome.config.Config() and every
    # Phase-1 FitConfig/DatasetRef in this repository declare it:
    from xtra_takehome.config import Config

    cfg = Config()
    assert cfg.ticker == "BZ=F"
    assert cfg.start == "2010-01-01"
    assert cfg.end == "2026-09-01"  # exclusive


def test_fixture_canonical_dataset_id_matches_the_golden_artifacts_provenance():
    """The decisive proof: hashing this committed fixture's decoded values
    (architecture plan Section 8.2's canonical dataset-identity scheme)
    reproduces the exact ``dataset_id`` recorded in the golden artifact's own
    provenance -- i.e. this fixture and the data that produced
    ``tests/fixtures/gjr_skewt_v1/`` are, semantically, the same dataset."""
    close = _load_fixture_close_series()
    ref = build_dataset_ref(
        ticker="BZ=F",
        start="2010-01-01",
        end="2026-09-01",
        close=close,
        uri="local://tests/fixtures/brent_dataset_v1/closes.csv",
    )

    golden_provenance = json.loads(GOLDEN_ARTIFACT_JSON.read_text())["provenance"]
    assert ref.dataset_id == golden_provenance["dataset_id"]
    # A concrete, human-checkable value, not just an equality between two
    # computed strings -- if this ever changes, it is loud.
    assert ref.dataset_id == (
        "sha256:e3c44c9e347067bce31a0f644d17fdac62414aa1a65e8885b5de431eb36c74cf"
    )
