from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .challenger import GjrSkewTGenerator
from .config import Config
from .data import fetch_close, log_returns_pct
from .model import GarchTGenerator
from .validation import (
    Gate,
    beyond_historical_max_fraction,
    pooled_context,
    validate,
    validate_horizon_matched,
)
from .windows import WindowStats, compute_window_stats, rolling_blocks


@dataclass(frozen=True)
class SeedRun:
    model: str
    seed: int
    pooled_passed: int
    pooled_total: int
    matched_passed: int
    matched_total: int
    beyond_historical_max: float
    pooled_metrics: dict[str, float]
    matched_metrics: dict[str, float]


def _extract(gates: list[Gate], name: str) -> float:
    for gate in gates:
        if gate.metric == name:
            return float(gate.synthetic)
    raise KeyError(name)


def baseline_fourth_moment_coefficient(model: GarchTGenerator) -> float:
    """Return E[(alpha z^2 + beta)^2] for standardized Student-t innovations."""
    if model.params_ is None:
        raise RuntimeError("Fit the model first.")
    p = model.params_
    if p.nu <= 4:
        return float("inf")
    ez4 = 3.0 * (p.nu - 2.0) / (p.nu - 4.0)
    return float((p.alpha**2) * ez4 + 2.0 * p.alpha * p.beta + p.beta**2)


TRACKED_METRICS = (
    "volatility",
    "skewness",
    "excess kurtosis",
    "q01",
    "q99",
    "VaR 99%",
    "ES 99%",
    "squared-return ACF MAE",
)


def evaluate_seed(
    model_name: str,
    model,
    returns,
    real_stats: WindowStats,
    mean_standard_error: float,
    cfg: Config,
    seed: int,
) -> SeedRun:
    paths = model.simulate(cfg.horizon, cfg.n_paths, seed)
    pooled, _, _ = validate(
        returns, paths, horizon=cfg.horizon, acf_lags=cfg.max_acf_lag
    )
    synthetic_stats = compute_window_stats(paths, acf_lags=cfg.max_acf_lag)
    matched = validate_horizon_matched(real_stats, synthetic_stats, mean_standard_error)

    return SeedRun(
        model=model_name,
        seed=seed,
        pooled_passed=sum(g.passed for g in pooled),
        pooled_total=len(pooled),
        matched_passed=sum(g.passed for g in matched),
        matched_total=len(matched),
        beyond_historical_max=beyond_historical_max_fraction(
            real_stats, synthetic_stats
        ),
        pooled_metrics={m: _extract(pooled, m) for m in TRACKED_METRICS},
        matched_metrics={m: _extract(matched, m) for m in TRACKED_METRICS},
    )


def _spread(runs: list[SeedRun], metric: str, family: str) -> tuple[float, float, float]:
    x = np.array([getattr(r, f"{family}_metrics")[metric] for r in runs], dtype=float)
    return float(np.median(x)), float(np.min(x)), float(np.max(x))


def _stability_rows(runs: list[SeedRun]) -> list[str]:
    rows = []
    for metric in TRACKED_METRICS:
        pm, plo, phi = _spread(runs, metric, "pooled")
        mm, mlo, mhi = _spread(runs, metric, "matched")
        pooled_span = abs(phi - plo) / max(abs(pm), 1e-8)
        matched_span = abs(mhi - mlo) / max(abs(mm), 1e-8)
        rows.append(
            f"| {metric} | {pm:.4f} [{plo:.4f}, {phi:.4f}] | {100*pooled_span:.0f}% | "
            f"{mm:.4f} [{mlo:.4f}, {mhi:.4f}] | {100*matched_span:.0f}% |"
        )
    return rows


def _passes(runs: list[SeedRun], family: str) -> tuple[float, int, int, int]:
    x = np.array([getattr(r, f"{family}_passed") for r in runs], dtype=float)
    total = getattr(runs[0], f"{family}_total")
    return float(np.median(x)), int(np.min(x)), int(np.max(x)), int(total)


def main() -> None:
    cfg = Config()
    returns = log_returns_pct(fetch_close(cfg.ticker, cfg.start, cfg.end))
    returns_array = returns.to_numpy()

    baseline = GarchTGenerator().fit(returns)
    challenger = GjrSkewTGenerator().fit(returns)
    assert baseline.params_ is not None
    assert challenger.params_ is not None

    real_stats = compute_window_stats(
        rolling_blocks(returns_array, cfg.horizon), acf_lags=cfg.max_acf_lag
    )
    mean_standard_error = pooled_context(
        returns_array, acf_lags=cfg.max_acf_lag
    ).mean_standard_error

    seeds = list(range(40, 50))
    baseline_runs = [
        evaluate_seed(
            "baseline", baseline, returns, real_stats, mean_standard_error, cfg, s
        )
        for s in seeds
    ]
    challenger_runs = [
        evaluate_seed(
            "challenger", challenger, returns, real_stats, mean_standard_error, cfg, s
        )
        for s in seeds
    ]

    baseline_fourth = baseline_fourth_moment_coefficient(baseline)
    challenger_fourth = challenger.params_.fourth_moment_coefficient

    bp = _passes(baseline_runs, "pooled")
    bm = _passes(baseline_runs, "matched")
    cp = _passes(challenger_runs, "pooled")
    cm = _passes(challenger_runs, "matched")
    b_beyond = np.array([r.beyond_historical_max for r in baseline_runs])
    c_beyond = np.array([r.beyond_historical_max for r in challenger_runs])

    pooled_kurt = _spread(challenger_runs, "excess kurtosis", "pooled")
    matched_kurt = _spread(challenger_runs, "excess kurtosis", "matched")
    pooled_skew = _spread(challenger_runs, "skewness", "pooled")

    lines = [
        "# Multi-seed robustness analysis",
        "",
        f"Both models are fitted once to the same historical returns and simulated over seeds {seeds[0]}-{seeds[-1]}. "
        "Each seed uses the same horizon, path count, metrics and tolerances, and draws from "
        "independent `SeedSequence` child streams so replications never share a generator "
        "stream. The purpose is not tuning; it is to separate structural behaviour from a "
        "single Monte Carlo realization, and to establish which estimator is stable enough "
        "to select a model with.",
        "",
        "## Analytical persistence and fourth-moment diagnostics",
        "",
        f"- Baseline GARCH(1,1)-t: `E[(alpha z^2 + beta)^2] = {baseline_fourth:.4f}`; "
        f"persistence {baseline.params_.persistence:.4f}; implied unconditional volatility "
        f"{baseline.params_.implied_unconditional_variance**0.5:.4f}%/day.",
        f"- Challenger GJR-skew-t: effective persistence "
        f"{challenger.params_.effective_persistence:.4f}, computed as "
        "`alpha + beta + gamma * E[z^2 I(z<0)]` under the fitted skew-t law; "
        f"`E[A(z)^2] = {challenger_fourth:.4f}`; implied unconditional volatility "
        f"{challenger.params_.implied_unconditional_variance**0.5:.4f}%/day against a sample "
        f"volatility of {returns.std(ddof=1):.4f}%/day; implied return tail index "
        f"{challenger.params_.implied_return_tail_index:.3f}.",
        "",
        "Both fitted specifications land in a near-integrated region that does not satisfy the "
        "finite unconditional fourth-moment condition on this sample. That is a property of "
        "these two fits, not of Brent itself: a different specification, or the same one on a "
        "different window, need not land there. Its consequence is that pooled sample kurtosis "
        "has no population value to converge to under either model, which is why the two "
        "estimator families below disagree so violently on that one statistic.",
        "",
        "## Which estimator can a decision be based on?",
        "",
        "| Model | Pooled gates (median, range) | Horizon-matched gates (median, range) | Years above any observed (median / worst) |",
        "|---|---:|---:|---:|",
        f"| baseline | {bp[0]:.1f}/{bp[3]} ({bp[1]}-{bp[2]}) | {bm[0]:.1f}/{bm[3]} ({bm[1]}-{bm[2]}) | "
        f"{100*np.median(b_beyond):.1f}% / {100*b_beyond.max():.1f}% |",
        f"| challenger | {cp[0]:.1f}/{cp[3]} ({cp[1]}-{cp[2]}) | {cm[0]:.1f}/{cm[3]} ({cm[1]}-{cm[2]}) | "
        f"{100*np.median(c_beyond):.1f}% / {100*c_beyond.max():.1f}% |",
        "",
        "No scalar aggregate score is reported. An earlier version summed per-gate "
        "error/threshold ratios, which the unbounded kurtosis ratio dominated so completely "
        "that the score was a restatement of one statistic; capping the ratio only replaced "
        "that problem with an arbitrary cap. Pass counts, the metric table below and the "
        "structural diagnostics above carry the information without inventing a number.",
        "",
        "The last column is named for what it measures and is not by itself a defect. With a "
        "record of only sixteen non-overlapping years, a correctly calibrated heavy-tailed "
        "generator *should* place a few per cent of years beyond anything observed; the "
        "validation report checks that frequency explicitly and finds it plausible for both.",
        "",
        "## Seed-to-seed stability of each statistic",
        "",
        "Median across seeds, full range in brackets, and the range as a percentage of the "
        "median. This is the core evidence for which estimator is a usable instrument.",
        "",
        "| Metric | Pooled: median [range] | Pooled spread | Matched: median [range] | Matched spread |",
        "|---|---:|---:|---:|---:|",
        *_stability_rows(challenger_runs),
        "",
        f"The contrast is specific rather than uniform, and worth stating precisely. Most "
        f"metrics are reasonably stable under both estimators. Two are not: pooled excess "
        f"kurtosis ranges over [{pooled_kurt[1]:.1f}, {pooled_kurt[2]:.1f}] across ten seeds "
        f"while the horizon-matched estimate of the same quantity stays within "
        f"[{matched_kurt[1]:.2f}, {matched_kurt[2]:.2f}], and pooled skewness ranges over "
        f"[{pooled_skew[1]:.2f}, {pooled_skew[2]:.2f}]. Those are exactly the two statistics "
        "that have no finite population value under a process without a fourth moment. A "
        "single seed of the pooled family can therefore report a kurtosis miss an order of "
        "magnitude larger than another seed of the identical model, which is why no number "
        "from that column should be quoted as characteristic of the generator.",
        "",
        "## Model-selection conclusion",
        "",
        "I select the **GJR-GARCH skew-t challenger**, on error magnitude rather than on any "
        "single gate. The clearest evidence is asymmetry: Brent's typical year has a markedly "
        f"negative return skew, and the challenger's horizon-matched median is "
        f"{_spread(challenger_runs, 'skewness', 'matched')[0]:.3f} against "
        f"{_spread(baseline_runs, 'skewness', 'matched')[0]:.3f} for the symmetric baseline, "
        "on a target no symmetric innovation law can reach by construction. Both models happen "
        "to clear the skewness tolerance, so this is a difference in fit quality rather than a "
        "gate outcome, and it would be inflation to call it decisive on the gate alone. The "
        "challenger also improves right-tail quantile calibration, where the baseline is the "
        "one that misses.",
        "",
        "The choice is not uniform dominance. The baseline is closer on some left-tail "
        "measures, and both models share the same structural limitation: a near-integrated "
        "variance recursion with no finite fourth moment. Selecting between them does not "
        "address that, and it is recorded as a model-risk finding inherited by whichever is "
        "chosen.",
        "",
        "In a production stress engine I would test GARCH-EVT for conditional tails and a "
        "regime-aware specification for the volatility-memory shape, with rolling-origin and "
        "regime holdouts, before treating either model as production-ready.",
    ]

    out = Path(cfg.output_dir) / "robustness_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    print(f"baseline   pooled {bp[0]:.1f}/{bp[3]}  matched {bm[0]:.1f}/{bm[3]}")
    print(f"challenger pooled {cp[0]:.1f}/{cp[3]}  matched {cm[0]:.1f}/{cm[3]}")


if __name__ == "__main__":
    main()
