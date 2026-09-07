"""Build and serialize a local ModelArtifact from the canonical Brent dataset.

Fits ``xtra_takehome``'s existing GJR-skew-t generator against the exact
ticker/window ``xtra_takehome.config.Config`` already pins, wraps the result
as a :class:`scenario_platform.domain.artifacts.ModelArtifact` via
``scenario_platform.domain.services.fit``, serializes it under ``--output``,
and prints its ``artifact_id`` and provenance for a human to record.

Deliberately a standalone script, not something ``python -m pytest`` ever
imports or runs: it fetches (or reads a locally cached copy of) live market
data, and the ordinary test suite must never depend on network access
(mirroring ``tests/test_end_to_end.py``'s own fabricated-series rationale).
Building the Phase-1 fixture is something a human runs once, deliberately,
the same way ``python -m xtra_takehome`` already is -- this script does not
introduce a new data-access pattern, it wraps the existing one
(``xtra_takehome.data.fetch_close`` against ``Config()``'s pinned window) in
the domain's own request/response types.

No AWS. No hidden mutable state: every value below is a pure function of the
data ``fetch_close`` returns, printed for inspection, never silently retried
against a different ticker/window if the requested one is unavailable.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scenario_platform.domain.artifacts import build_dataset_ref
from scenario_platform.domain.requests import FitConfig
from scenario_platform.domain.serialization import save_artifact
from scenario_platform.domain.services import fit
from xtra_takehome.config import Config
from xtra_takehome.data import fetch_close, log_returns_pct


def build(*, model_version: str, output: Path, cache_dir: Path) -> None:
    cfg = Config()
    print(
        f"Fetching {cfg.ticker}: {cfg.start} .. {cfg.end} (end exclusive), "
        f"cache={cache_dir}"
    )
    close = fetch_close(cfg.ticker, cfg.start, cfg.end, cache_dir=cache_dir)
    returns = log_returns_pct(close)
    print(f"{len(close):,} closes -> {len(returns):,} returns")

    dataset = build_dataset_ref(
        ticker=cfg.ticker,
        start=cfg.start,
        end=cfg.end,
        close=close,
        uri=f"local://{cache_dir}/{cfg.ticker}_{cfg.start}_{cfg.end}".replace("=", ""),
    )
    print(f"dataset_id: {dataset.dataset_id}")

    fit_config = FitConfig(dataset=dataset, model_version=model_version)
    print("Fitting GJR-GARCH(1,1,1) with Hansen skewed-t innovations ...")
    artifact = fit(returns, fit_config)

    print(f"artifact_id:   {artifact.artifact_id}")
    print(f"model_version: {artifact.model_version}")
    print(f"schema_version: {artifact.schema_version}")
    print("params:")
    for name in ("mu", "omega", "alpha", "gamma", "beta", "eta", "lam"):
        print(f"  {name}: {getattr(artifact.params, name)!r}")
    print("diagnostics:")
    for field in (
        "effective_persistence",
        "fourth_moment_coefficient",
        "implied_unconditional_variance",
        "implied_return_tail_index",
        "hill_tail_index",
        "finite_second_moment",
        "finite_third_moment",
        "finite_fourth_moment",
    ):
        print(f"  {field}: {getattr(artifact.diagnostics, field)!r}")
    print("provenance:")
    print(f"  dataset_id: {artifact.provenance.dataset_id}")
    print(f"  dataset_uri: {artifact.provenance.dataset_uri}")
    print(
        f"  calibration_window: {artifact.provenance.calibration_window_start} .. "
        f"{artifact.provenance.calibration_window_end}"
    )
    print(f"  calibration_timestamp: {artifact.provenance.calibration_timestamp}")

    save_artifact(artifact, output)
    print(f"Artifact written to {output}/ (artifact.json + state.npz)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-version",
        required=True,
        help='Human-readable registry handle, e.g. "gjr-skewt-20260907-1".',
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Directory to write the artifact into."
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache"),
        help="Local, gitignored price-data cache (default: .cache).",
    )
    args = parser.parse_args(argv)
    build(model_version=args.model_version, output=args.output, cache_dir=args.cache_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
