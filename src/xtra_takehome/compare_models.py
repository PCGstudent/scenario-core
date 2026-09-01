from __future__ import annotations

from pathlib import Path

from .challenger import GjrSkewTGenerator
from .config import Config
from .data import fetch_close, log_returns_pct
from .model import GarchTGenerator
from .validation import validate


def _score(gates) -> tuple[int, float]:
    passed = sum(g.passed for g in gates)
    normalized_error = 0.0
    for g in gates:
        if g.threshold <= 0:
            continue
        normalized_error += g.error / g.threshold
    return passed, normalized_error


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

    b_pass, b_err = _score(baseline_gates)
    c_pass, c_err = _score(challenger_gates)
    winner = "challenger" if (c_pass > b_pass or (c_pass == b_pass and c_err < b_err)) else "baseline"

    print(f"Baseline:   {b_pass}/{len(baseline_gates)} gates, normalized error={b_err:.3f}")
    print(f"Challenger: {c_pass}/{len(challenger_gates)} gates, normalized error={c_err:.3f}")
    print(f"Provisional winner: {winner}")

    assert baseline.params_ is not None
    assert challenger.params_ is not None

    lines = [
        "# Baseline vs challenger",
        "",
        "This development comparison uses the same data, horizon, number of paths, seed, validation metrics, and acceptance gates for both models.",
        "",
        "## Models",
        "",
        "- **Baseline:** GARCH(1,1) with Student-t innovations.",
        "- **Challenger:** GJR-GARCH(1,1,1) with Hansen skewed-t innovations and empirical fitted-state initialization.",
        "",
        f"Baseline gates passed: **{b_pass}/{len(baseline_gates)}**; aggregate normalized gate error: **{b_err:.3f}**.",
        f"Challenger gates passed: **{c_pass}/{len(challenger_gates)}**; aggregate normalized gate error: **{c_err:.3f}**.",
        f"Provisional winner under the declared rule: **{winner}**.",
        "",
        "The pass count is the primary criterion; aggregate error relative to each declared threshold is only a tie-breaker. This is a development aid, not an excuse to tune thresholds after observing results.",
        "",
        "## Fitted challenger parameters",
        "",
        f"- mu: {challenger.params_.mu:.6f}",
        f"- omega: {challenger.params_.omega:.6f}",
        f"- alpha: {challenger.params_.alpha:.6f}",
        f"- gamma: {challenger.params_.gamma:.6f}",
        f"- beta: {challenger.params_.beta:.6f}",
        f"- approximate persistence alpha + gamma/2 + beta: {challenger.params_.approximate_persistence:.6f}",
        f"- skew-t eta: {challenger.params_.eta:.6f}",
        f"- skew-t lambda: {challenger.params_.lam:.6f}",
        "",
        "## Gate-by-gate comparison",
        "",
        "| Model | Metric | Real | Synthetic | Error | Threshold | Status |",
        "|---|---|---:|---:|---:|---:|:---:|",
        *_table("baseline", baseline_gates),
        *_table("challenger", challenger_gates),
        "",
        "## Decision note",
        "",
        "The challenger is justified only if the observed negative skew / volatility asymmetry and validation results improve enough to warrant the extra parameterization. If not, the simpler baseline should remain the submission model.",
    ]

    out = Path(cfg.output_dir) / "model_comparison.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
