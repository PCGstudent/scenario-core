from __future__ import annotations

from pathlib import Path

import numpy as np

from .challenger import GjrSkewTGenerator
from .config import Config
from .data import fetch_close, log_returns_pct
from .model import GarchTGenerator
from .robustness import capped_normalized_error
from .validation import Gate, validate, validate_horizon_matched
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
        rows.append(
            f"| {name} | {g.metric} | {g.real:.4f} | {g.synthetic:.4f} | {err} | {thr} | "
            f"{'PASS' if g.passed else 'FAIL'} |"
        )
    return rows


def main() -> None:
    cfg = Config()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {cfg.ticker}: {cfg.start} -> {cfg.end}")
    returns = log_returns_pct(fetch_close(cfg.ticker, cfg.start, cfg.end))
    real_stats = compute_window_stats(
        rolling_blocks(returns.to_numpy(), cfg.horizon), acf_lags=cfg.max_acf_lag
    )

    results = {}
    for name, generator in [
        ("baseline", GarchTGenerator()),
        ("challenger", GjrSkewTGenerator()),
    ]:
        print(f"Fitting {name}...")
        generator.fit(returns)
        paths = generator.simulate(cfg.horizon, cfg.n_paths, cfg.seed)
        pooled, _ = validate(
            returns, paths, horizon=cfg.horizon, acf_lags=cfg.max_acf_lag
        )
        synthetic_stats = compute_window_stats(paths, acf_lags=cfg.max_acf_lag)
        matched = validate_horizon_matched(real_stats, synthetic_stats)
        results[name] = {
            "generator": generator,
            "pooled": pooled,
            "matched": matched,
            "explosive": float(
                np.mean(synthetic_stats.volatility > np.max(real_stats.volatility))
            ),
        }

    b, c = results["baseline"], results["challenger"]
    total = len(b["pooled"])

    for name, r in results.items():
        print(
            f"{name}: pooled {sum(g.passed for g in r['pooled'])}/{total}, "
            f"matched {sum(g.passed for g in r['matched'])}/{total}, "
            f"explosive years {100*r['explosive']:.1f}%"
        )
    print("No automatic winner is declared from this single seed; see robustness_report.md.")

    challenger_params = c["generator"].params_
    baseline_params = b["generator"].params_
    assert challenger_params is not None and baseline_params is not None

    lines = [
        "# Baseline vs challenger",
        "",
        "This is a development comparison, not the final model-selection rule. Both models use "
        "the same data, horizon, number of paths, seed, fitted-state initialization principle, "
        "validation metrics, and the single declared threshold table in `validation.THRESHOLDS`.",
        "",
        "## Models",
        "",
        "- **Baseline:** GARCH(1,1) with Student-t innovations.",
        "- **Challenger / submitted model:** GJR-GARCH(1,1,1) with Hansen skewed-t innovations.",
        "",
        "Every metric is scored twice: once on the pooled synthetic sample, and once with each "
        "statistic estimated inside 252-day blocks on both sides. The two families share their "
        "thresholds and differ only in the estimator.",
        "",
        "| Model | Pooled gates | Horizon-matched gates | Pooled capped error | Matched capped error | Years more volatile than any observed |",
        "|---|---:|---:|---:|---:|---:|",
        f"| baseline | {sum(g.passed for g in b['pooled'])}/{total} | "
        f"{sum(g.passed for g in b['matched'])}/{total} | "
        f"{capped_normalized_error(b['pooled']):.2f} | "
        f"{capped_normalized_error(b['matched']):.2f} | {100*b['explosive']:.1f}% |",
        f"| challenger | {sum(g.passed for g in c['pooled'])}/{total} | "
        f"{sum(g.passed for g in c['matched'])}/{total} | "
        f"{capped_normalized_error(c['pooled']):.2f} | "
        f"{capped_normalized_error(c['matched']):.2f} | {100*c['explosive']:.1f}% |",
        "",
        "I deliberately do **not** declare a winner from this one realization. An early "
        "AI-assisted comparison used pass count as the primary winner rule; reviewing the tail "
        "errors showed that this was too simplistic, and the multi-seed analysis later showed "
        "that the pooled scorecard is not even stable enough to rank two models with. The "
        "selection decision therefore uses `robustness_report.md`, the horizon-matched family, "
        "failure severity, and structural diagnostics.",
        "",
        "## Fitted parameters",
        "",
        "| Quantity | Baseline | Challenger |",
        "|---|---:|---:|",
        f"| mu | {baseline_params.mu:.6f} | {challenger_params.mu:.6f} |",
        f"| omega | {baseline_params.omega:.6f} | {challenger_params.omega:.6f} |",
        f"| alpha | {baseline_params.alpha:.6f} | {challenger_params.alpha:.6f} |",
        f"| gamma | - | {challenger_params.gamma:.6f} |",
        f"| beta | {baseline_params.beta:.6f} | {challenger_params.beta:.6f} |",
        f"| effective persistence | {baseline_params.persistence:.6f} | "
        f"{challenger_params.effective_persistence:.6f} |",
        f"| implied unconditional volatility (%/day) | "
        f"{baseline_params.implied_unconditional_variance**0.5:.4f} | "
        f"{challenger_params.implied_unconditional_variance**0.5:.4f} |",
        f"| innovation shape | nu = {baseline_params.nu:.4f} | "
        f"eta = {challenger_params.eta:.4f}, lambda = {challenger_params.lam:.4f} |",
        "",
        "For the skewed innovation law, effective persistence uses "
        "`alpha + beta + gamma * E[z^2 I(z<0)]`; the symmetric `gamma/2` approximation is not "
        "used. Both models imply an unconditional volatility above the sample volatility of "
        f"{returns.std(ddof=1):.4f}%/day, which is the source of the explosive paths reported "
        "in the last column of the table above.",
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
        "materially better and improves right-tail quantiles and extreme drawdown calibration, "
        "while the added structure remains small and interpretable. It is not uniformly better: "
        "the baseline is somewhat closer on some left-tail measures. Under the pooled family "
        "the challenger's kurtosis and skewness misses look catastrophic, but the multi-seed "
        "analysis shows those pooled statistics swinging by several hundred per cent across "
        "seeds for both models, so they are not a basis for ranking. Both models share the "
        "near-integrated variance recursion and the resulting explosive years; that is recorded "
        "as a model-risk finding inherited by whichever is selected, not averaged into a score.",
    ]

    out = Path(cfg.output_dir) / "model_comparison.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
