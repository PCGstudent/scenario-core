from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .challenger import GjrSkewTGenerator
from .config import Config
from .data import fetch_close, log_returns_pct
from .model import GarchTGenerator
from .validation import Gate, validate


@dataclass(frozen=True)
class SeedRun:
    model: str
    seed: int
    passed: int
    total: int
    normalized_error: float
    severe_failures: int
    metrics: dict[str, float]


def _normalized_error(gates: list[Gate]) -> float:
    return float(
        sum(g.error / g.threshold for g in gates if g.threshold > 0)
    )


def _severe_failures(gates: list[Gate], multiple: float = 2.0) -> int:
    return sum(
        (not g.passed) and g.threshold > 0 and g.error > multiple * g.threshold
        for g in gates
    )


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


def evaluate_seed(model_name: str, model, returns, cfg: Config, seed: int) -> SeedRun:
    paths = model.simulate(cfg.horizon, cfg.n_paths, seed)
    gates, _ = validate(
        returns,
        paths,
        horizon=cfg.horizon,
        acf_lags=cfg.max_acf_lag,
    )
    metrics = {
        name: _extract(gates, name)
        for name in [
            "volatility",
            "skewness",
            "excess kurtosis",
            "q01",
            "q99",
            "VaR 99%",
            "ES 99%",
            "squared-return ACF MAE (lags 1-20)",
            "drawdown p95",
        ]
    }
    return SeedRun(
        model=model_name,
        seed=seed,
        passed=sum(g.passed for g in gates),
        total=len(gates),
        normalized_error=_normalized_error(gates),
        severe_failures=_severe_failures(gates),
        metrics=metrics,
    )


def _summarize_runs(runs: list[SeedRun]) -> dict[str, float]:
    passes = np.array([r.passed for r in runs], dtype=float)
    errors = np.array([r.normalized_error for r in runs], dtype=float)
    severe = np.array([r.severe_failures for r in runs], dtype=float)
    return {
        "median_passed": float(np.median(passes)),
        "min_passed": float(np.min(passes)),
        "max_passed": float(np.max(passes)),
        "median_normalized_error": float(np.median(errors)),
        "max_normalized_error": float(np.max(errors)),
        "median_severe_failures": float(np.median(severe)),
        "max_severe_failures": float(np.max(severe)),
    }


def _metric_summary(runs: list[SeedRun], metric: str) -> tuple[float, float, float]:
    x = np.array([r.metrics[metric] for r in runs], dtype=float)
    return float(np.median(x)), float(np.quantile(x, 0.10)), float(np.quantile(x, 0.90))


def main() -> None:
    cfg = Config()
    returns = log_returns_pct(fetch_close(cfg.ticker, cfg.start, cfg.end))

    baseline = GarchTGenerator().fit(returns)
    challenger = GjrSkewTGenerator().fit(returns)
    assert baseline.params_ is not None
    assert challenger.params_ is not None

    seeds = list(range(40, 50))
    baseline_runs = [evaluate_seed("baseline", baseline, returns, cfg, s) for s in seeds]
    challenger_runs = [evaluate_seed("challenger", challenger, returns, cfg, s) for s in seeds]

    b_summary = _summarize_runs(baseline_runs)
    c_summary = _summarize_runs(challenger_runs)
    baseline_fourth = baseline_fourth_moment_coefficient(baseline)
    challenger_fourth = challenger.params_.fourth_moment_coefficient
    challenger_persistence = challenger.params_.effective_persistence

    metric_names = [
        "volatility",
        "skewness",
        "excess kurtosis",
        "q01",
        "q99",
        "VaR 99%",
        "ES 99%",
        "squared-return ACF MAE (lags 1-20)",
        "drawdown p95",
    ]

    lines = [
        "# Multi-seed robustness analysis",
        "",
        "Both models are fitted once to the same historical returns and simulated over seeds 40-49. Each seed uses the same horizon, number of paths, validation metrics, fitted-state initialization principle, and fixed acceptance gates. The purpose is not hyperparameter tuning; it is to distinguish structural behaviour from a single Monte Carlo realization.",
        "",
        "## Analytical persistence / fourth-moment diagnostics",
        "",
        f"- Baseline GARCH(1,1)-t: `E[(alpha z^2 + beta)^2] = {baseline_fourth:.4f}`.",
        f"- Challenger GJR-skew-t effective persistence: `{challenger_persistence:.4f}`, computed as `alpha + beta + gamma * E[z^2 I(z<0)]` under the fitted skew-t law.",
        f"- Challenger GJR-skew-t fourth-moment coefficient: `E[A(z)^2] = {challenger_fourth:.4f}`, with `A(z)=beta + alpha*z^2 + gamma*z^2*I(z<0)`.",
        "",
        (
            "The baseline does not satisfy the usual finite unconditional fourth-moment condition; sample kurtosis is therefore intrinsically unstable across simulations."
            if baseline_fourth >= 1.0
            else "The baseline satisfies the usual finite unconditional fourth-moment condition."
        ),
        (
            "The challenger also does not satisfy the usual finite unconditional fourth-moment condition; this provides a structural explanation for unstable simulated kurtosis."
            if challenger_fourth >= 1.0
            else "The challenger satisfies the usual finite unconditional fourth-moment condition."
        ),
        "",
        "## Aggregate stability",
        "",
        "| Model | Median gates | Range | Median normalized error | Worst normalized error | Median severe fails | Worst severe fails |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| baseline | {b_summary['median_passed']:.1f}/15 | {b_summary['min_passed']:.0f}-{b_summary['max_passed']:.0f} | {b_summary['median_normalized_error']:.2f} | {b_summary['max_normalized_error']:.2f} | {b_summary['median_severe_failures']:.1f} | {b_summary['max_severe_failures']:.0f} |",
        f"| challenger | {c_summary['median_passed']:.1f}/15 | {c_summary['min_passed']:.0f}-{c_summary['max_passed']:.0f} | {c_summary['median_normalized_error']:.2f} | {c_summary['max_normalized_error']:.2f} | {c_summary['median_severe_failures']:.1f} | {c_summary['max_severe_failures']:.0f} |",
        "",
        "## Metric stability (median [10th, 90th percentile])",
        "",
        "| Metric | Baseline | Challenger |",
        "|---|---:|---:|",
    ]

    for metric in metric_names:
        bm, b10, b90 = _metric_summary(baseline_runs, metric)
        cm, c10, c90 = _metric_summary(challenger_runs, metric)
        lines.append(
            f"| {metric} | {bm:.4f} [{b10:.4f}, {b90:.4f}] | {cm:.4f} [{c10:.4f}, {c90:.4f}] |"
        )

    lines += [
        "",
        "## Model-selection conclusion",
        "",
        "I select the **GJR-GARCH skew-t challenger** for the submitted generator. Its improvements in negative skew, upper/lower quantiles and drawdown behaviour are persistent across seeds, and the additional structure is still small and interpretable. I do not select it because it merely has more PASS labels: normalized error, severe failures and analytical tail diagnostics are reviewed explicitly.",
        "",
        "The selection is conditional, not a claim of adequacy. Higher-moment instability and squared-return ACF mismatch remain model-risk findings. In a production stress engine I would test GARCH-EVT for conditional tails and/or regime-aware volatility, with rolling/regime holdouts, before treating either model as production-ready.",
    ]

    out = Path(cfg.output_dir) / "robustness_report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")

    print("Baseline summary:", b_summary)
    print("Challenger summary:", c_summary)
    print(f"Baseline fourth-moment coefficient: {baseline_fourth:.4f}")
    print(f"Challenger effective persistence: {challenger_persistence:.4f}")
    print(f"Challenger fourth-moment coefficient: {challenger_fourth:.4f}")


if __name__ == "__main__":
    main()
