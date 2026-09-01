from __future__ import annotations

from pathlib import Path


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
from .windows import compute_window_stats, rolling_blocks


def _table(name: str, gates: list[Gate]) -> list[str]:
    rows = []
    for g in gates:
        err = f"{100*g.error:.1f}%" if g.error_type == "relative" else f"{g.error:.4f}"
        thr = (
            f"{100*g.threshold:.0f}%"
            if g.error_type == "relative"
            else f"{g.threshold:.4f}"
        )
        real = "-" if g.metric == "squared-return ACF MAE" else f"{g.real:.4f}"
        rows.append(
            f"| {name} | {g.metric} | {real} | {g.synthetic:.4f} | {err} | {thr} | "
            f"{'PASS' if g.passed else 'FAIL'} |"
        )
    return rows


def main() -> None:
    cfg = Config()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {cfg.ticker}: {cfg.start} -> {cfg.end}")
    returns = log_returns_pct(fetch_close(cfg.ticker, cfg.start, cfg.end))
    returns_array = returns.to_numpy()
    real_stats = compute_window_stats(
        rolling_blocks(returns_array, cfg.horizon), acf_lags=cfg.max_acf_lag
    )
    mean_standard_error = pooled_context(
        returns_array, acf_lags=cfg.max_acf_lag
    ).mean_standard_error

    results = {}
    for name, generator in [
        ("baseline", GarchTGenerator()),
        ("challenger", GjrSkewTGenerator()),
    ]:
        print(f"Fitting {name}...")
        generator.fit(returns)
        paths = generator.simulate(cfg.horizon, cfg.n_paths, cfg.seed)
        pooled, _, _ = validate(
            returns, paths, horizon=cfg.horizon, acf_lags=cfg.max_acf_lag
        )
        synthetic_stats = compute_window_stats(paths, acf_lags=cfg.max_acf_lag)
        matched = validate_horizon_matched(
            real_stats, synthetic_stats, mean_standard_error
        )
        results[name] = {
            "generator": generator,
            "pooled": pooled,
            "matched": matched,
            "beyond": beyond_historical_max_fraction(real_stats, synthetic_stats),
        }

    b, c = results["baseline"], results["challenger"]
    pooled_total, matched_total = len(b["pooled"]), len(b["matched"])

    for name, r in results.items():
        print(
            f"{name}: pooled {sum(g.passed for g in r['pooled'])}/{pooled_total}, "
            f"matched {sum(g.passed for g in r['matched'])}/{matched_total}, "
            f"years beyond any observed {100*r['beyond']:.1f}%"
        )
    print("No automatic winner from this single seed; see robustness_report.md.")

    cp, bp = c["generator"].params_, b["generator"].params_
    assert cp is not None and bp is not None

    lines = [
        "# Baseline vs challenger",
        "",
        "A development comparison, not the final selection rule. Both models use the same "
        "data, horizon, path count, seed, fitted-state initialization principle, metrics "
        "and tolerances.",
        "",
        "## Models",
        "",
        "- **Baseline:** GARCH(1,1) with Student-t innovations.",
        "- **Challenger / submitted model:** GJR-GARCH(1,1,1) with Hansen skewed-t innovations.",
        "",
        "Every metric is scored under two estimators: once on the pooled synthetic sample, "
        "and once with each statistic estimated inside 252-day blocks on both sides. "
        "Tolerances are identical; only the measurement differs. Where a tolerance needs a "
        "scale it is re-derived from the historical sample under the estimator in use, which "
        "is what holding strictness constant actually requires.",
        "",
        "| Model | Pooled gates | Horizon-matched gates | Years more volatile than any observed |",
        "|---|---:|---:|---:|",
        f"| baseline | {sum(g.passed for g in b['pooled'])}/{pooled_total} | "
        f"{sum(g.passed for g in b['matched'])}/{matched_total} | {100*b['beyond']:.1f}% |",
        f"| challenger | {sum(g.passed for g in c['pooled'])}/{pooled_total} | "
        f"{sum(g.passed for g in c['matched'])}/{matched_total} | {100*c['beyond']:.1f}% |",
        "",
        "I deliberately do **not** declare a winner from this one realization. An early "
        "AI-assisted comparison used pass count as the winner rule; reviewing the tail errors "
        "showed that was too simplistic, and the multi-seed analysis later showed the pooled "
        "scorecard is not stable enough to rank two models with at all. The decision uses "
        "`robustness_report.md`, the horizon-matched family and the structural diagnostics.",
        "",
        "## Fitted parameters",
        "",
        "| Quantity | Baseline | Challenger |",
        "|---|---:|---:|",
        f"| mu | {bp.mu:.6f} | {cp.mu:.6f} |",
        f"| omega | {bp.omega:.6f} | {cp.omega:.6f} |",
        f"| alpha | {bp.alpha:.6f} | {cp.alpha:.6f} |",
        f"| gamma | - | {cp.gamma:.6f} |",
        f"| beta | {bp.beta:.6f} | {cp.beta:.6f} |",
        f"| effective persistence | {bp.persistence:.6f} | {cp.effective_persistence:.6f} |",
        f"| implied unconditional volatility (%/day) | "
        f"{bp.implied_unconditional_variance**0.5:.4f} | "
        f"{cp.implied_unconditional_variance**0.5:.4f} |",
        f"| innovation shape | nu = {bp.nu:.4f} | "
        f"eta = {cp.eta:.4f}, lambda = {cp.lam:.4f} |",
        f"| implied return tail index | - | {cp.implied_return_tail_index:.4f} |",
        "",
        "For the skewed innovation law, effective persistence uses "
        "`alpha + beta + gamma * E[z^2 I(z<0)]`; the symmetric `gamma/2` approximation is not "
        "used. Both models imply an unconditional volatility above the sample volatility of "
        f"{returns.std(ddof=1):.4f}%/day, and both are near-integrated.",
        "",
        "## Gate-by-gate comparison, pooled",
        "",
        "| Model | Metric | Real | Synthetic | Error | Threshold | Status |",
        "|---|---|---:|---:|---:|---:|:---:|",
        *_table("baseline", b["pooled"]),
        *_table("challenger", c["pooled"]),
        "",
        "## Gate-by-gate comparison, horizon-matched",
        "",
        "| Model | Metric | Real median | Synthetic median | Error | Threshold | Status |",
        "|---|---|---:|---:|---:|---:|:---:|",
        *_table("baseline", b["matched"]),
        *_table("challenger", c["matched"]),
        "",
        "## Selection rationale",
        "",
        "The asymmetric challenger is retained because it captures the observed negative skew "
        "materially better and improves right-tail quantile calibration, while the added "
        "structure remains small and interpretable. It is not uniformly better: the baseline "
        "is closer on some left-tail measures. Under the pooled family the challenger's "
        "kurtosis and skewness misses look catastrophic, but the multi-seed analysis shows "
        "those pooled statistics ranging over several hundred per cent across seeds for both "
        "models, so they are not a basis for ranking. Both models share the near-integrated "
        "variance recursion and its consequences; that is recorded as a model-risk finding "
        "inherited by whichever is selected, not averaged into a score.",
    ]

    out = Path(cfg.output_dir) / "model_comparison.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
