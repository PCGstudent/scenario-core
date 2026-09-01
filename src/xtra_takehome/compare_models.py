from __future__ import annotations

from pathlib import Path

from .challenger import GjrSkewTGenerator
from .config import Config
from .data import fetch_close, log_returns_pct
from .model import GarchTGenerator
from .validation import validate


def _score(gates) -> tuple[int, float, int]:
    passed = sum(g.passed for g in gates)
    normalized_error = 0.0
    severe_failures = 0
    for g in gates:
        if g.threshold <= 0:
            continue
        ratio = g.error / g.threshold
        normalized_error += ratio
        if (not g.passed) and ratio > 2.0:
            severe_failures += 1
    return passed, float(normalized_error), severe_failures


def _table(name, gates):
    rows = []
    for g in gates:
        err = f"{100*g.error:.1f}%" if g.error_type == "relative" else f"{g.error:.4f}"
        thr = f"{100*g.threshold:.0f}%" if g.error_type == "relative" else f"{g.threshold:.4f}"
        rows.append(
            f"| {name} | {g.metric} | {g.real:.4f} | {g.synthetic:.4f} | {err} | {thr} | {'PASS' if g.passed else 'FAIL'} |"
        )
    return rows


def main() -> None:
    cfg = Config()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {cfg.ticker}: {cfg.start} -> {cfg.end}")
    returns = log_returns_pct(fetch_close(cfg.ticker, cfg.start, cfg.end))

    print("Fitting baseline GARCH(1,1)-t...")
    baseline = GarchTGenerator().fit(returns)
    baseline_paths = baseline.simulate(cfg.horizon, cfg.n_paths, cfg.seed)
    baseline_gates, _ = validate(
        returns, baseline_paths, horizon=cfg.horizon, acf_lags=cfg.max_acf_lag
    )

    print("Fitting challenger GJR-GARCH(1,1,1)-skew-t...")
    challenger = GjrSkewTGenerator().fit(returns)
    challenger_paths = challenger.simulate(cfg.horizon, cfg.n_paths, cfg.seed)
    challenger_gates, _ = validate(
        returns, challenger_paths, horizon=cfg.horizon, acf_lags=cfg.max_acf_lag
    )

    b_pass, b_err, b_severe = _score(baseline_gates)
    c_pass, c_err, c_severe = _score(challenger_gates)

    print(
        f"Baseline:   {b_pass}/{len(baseline_gates)} gates, "
        f"normalized error={b_err:.3f}, severe fails={b_severe}"
    )
    print(
        f"Challenger: {c_pass}/{len(challenger_gates)} gates, "
        f"normalized error={c_err:.3f}, severe fails={c_severe}"
    )
    print("No automatic winner is declared from this single seed; see robustness_report.md.")

    assert baseline.params_ is not None
    assert challenger.params_ is not None

    lines = [
        "# Baseline vs challenger",
        "",
        "This is a development comparison, not the final model-selection rule. Both models use the same data, horizon, number of paths, seed, fitted-state initialization principle, validation metrics, and acceptance gates.",
        "",
        "## Models",
        "",
        "- **Baseline:** GARCH(1,1) with Student-t innovations.",
        "- **Challenger / submitted model:** GJR-GARCH(1,1,1) with Hansen skewed-t innovations.",
        "",
        f"Baseline: **{b_pass}/{len(baseline_gates)}** gates; aggregate normalized gate error **{b_err:.3f}**; severe failures **{b_severe}**.",
        f"Challenger: **{c_pass}/{len(challenger_gates)}** gates; aggregate normalized gate error **{c_err:.3f}**; severe failures **{c_severe}**.",
        "",
        "I deliberately do **not** declare a winner from this one realization. An early AI-assisted comparison used pass count as the primary winner rule; review of the tail errors showed that this was too simplistic. The selection decision therefore uses the multi-seed robustness analysis in `robustness_report.md`, failure severity, metric relevance and model interpretability in addition to this table.",
        "",
        "## Fitted challenger parameters",
        "",
        f"- mu: {challenger.params_.mu:.6f}",
        f"- omega: {challenger.params_.omega:.6f}",
        f"- alpha: {challenger.params_.alpha:.6f}",
        f"- gamma: {challenger.params_.gamma:.6f}",
        f"- beta: {challenger.params_.beta:.6f}",
        f"- effective persistence: {challenger.params_.effective_persistence:.6f}",
        f"- skew-t eta: {challenger.params_.eta:.6f}",
        f"- skew-t lambda: {challenger.params_.lam:.6f}",
        f"- fourth-moment coefficient E[A(z)^2]: {challenger.params_.fourth_moment_coefficient:.6f}",
        "",
        "For the skewed innovation law, effective persistence uses `alpha + beta + gamma * E[z^2 I(z<0)]`; the symmetric `gamma/2` approximation is not used.",
        "",
        "## Gate-by-gate comparison",
        "",
        "| Model | Metric | Real | Synthetic | Error | Threshold | Status |",
        "|---|---|---:|---:|---:|---:|:---:|",
        *_table("baseline", baseline_gates),
        *_table("challenger", challenger_gates),
        "",
        "## Selection rationale",
        "",
        "The asymmetric challenger is retained because it captures the observed negative skew materially better and improves right-tail quantiles and extreme drawdown calibration while the added structure remains small and interpretable. It is not uniformly better: the baseline is somewhat closer on some left-tail q01/VaR/ES measures, although those challenger measures remain inside the declared gates. The challenger's much larger kurtosis miss also makes its aggregate normalized error worse; that is treated as an explicit higher-moment model-risk finding rather than averaged away or used as a reason to move thresholds.",
    ]

    out = Path(cfg.output_dir) / "model_comparison.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
