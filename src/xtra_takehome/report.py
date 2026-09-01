from __future__ import annotations

from pathlib import Path

from .diagnostics import DiagnosticSummary
from .model import GarchTParams
from .validation import Gate


def _fmt(x: float) -> str:
    if abs(x) >= 100:
        return f"{x:.2f}"
    if abs(x) >= 1:
        return f"{x:.3f}"
    return f"{x:.4f}"


def interpretation(summary: DiagnosticSummary) -> str:
    return (
        f"The sample contains **{summary.n:,}** daily observations. Mean daily log return "
        f"is {_fmt(summary.mean)}% with volatility {_fmt(summary.std)}%. The distribution "
        f"is {'negatively' if summary.skew < 0 else 'positively'} skewed "
        f"({_fmt(summary.skew)}) and has excess kurtosis {_fmt(summary.excess_kurtosis)}, "
        f"which is inconsistent with a thin-tailed Gaussian description. A fitted Student-t "
        f"reference has approximately **{summary.student_t_df:.2f} degrees of freedom**, "
        f"providing a compact heavy-tailed benchmark for the QQ diagnostic.\n\n"
        f"Linear return dependence is comparatively limited: the maximum absolute return ACF "
        f"over the inspected non-zero lags is {_fmt(summary.max_abs_return_acf)}. In contrast, "
        f"the mean absolute squared-return ACF is {_fmt(summary.mean_abs_squared_acf)}, which "
        f"is evidence of conditional heteroskedasticity / volatility clustering. That diagnostic "
        f"directly motivates a volatility-aware generator rather than iid Monte Carlo.\n\n"
        f"I therefore use a **GARCH(1,1) with Student-t innovations** as a parsimonious baseline: "
        f"GARCH targets volatility persistence while Student-t innovations target heavy marginal "
        f"tails. The model does not claim to create genuinely new geopolitical regimes, asymmetric "
        f"shock responses, or multivariate market dependence. Those are intentionally left as "
        f"extensions if validation exposes material tail or regime failures."
    )


def write_report(
    output_path: Path,
    summary: DiagnosticSummary,
    params: GarchTParams,
    gates: list[Gate],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for g in gates:
        error = f"{100*g.error:.1f}%" if g.error_type == "relative" else _fmt(g.error)
        threshold = (
            f"{100*g.threshold:.0f}%"
            if g.error_type == "relative"
            else _fmt(g.threshold)
        )
        rows.append(
            f"| {g.metric} | {_fmt(g.real)} | {_fmt(g.synthetic)} | "
            f"{error} | {threshold} | {'PASS' if g.passed else 'FAIL'} |"
        )

    failures = [g for g in gates if not g.passed]
    if failures:
        fail_text = "\n".join(
            f"- **{g.metric}** fails its declared gate. This is retained as evidence rather "
            f"than tuned away." for g in failures
        )
        failure_discussion = (
            f"{fail_text}\n\n"
            "The first extension I would test is conditional-tail modelling of standardized "
            "GARCH residuals using Peaks Over Threshold / Generalized Pareto tails (GARCH-EVT), "
            "especially if the failures concentrate in 99% ES, extreme quantiles, or drawdowns. "
            "If failures instead reflect state-dependent persistence, a regime-switching or "
            "asymmetric volatility model would be more appropriate."
        )
    else:
        failure_discussion = (
            "All declared engineering gates pass on this run, but that should not be read as "
            "proof of future predictive validity. The principal failure mode remains structural: "
            "a single stationary GARCH-t process cannot represent genuinely new regimes or "
            "geopolitical mechanisms. A regime holdout (for example pre-shock fit versus later "
            "stress period) would be the next validation step."
        )

    text = f"""# Brent synthetic-scenario validation report

## Scope

Daily Brent crude `BZ=F` close prices are fetched in code from Yahoo Finance. The analysis uses percentage log returns and a fixed historical cut-off for reproducibility.

## Statistical diagnostics

{interpretation(summary)}

![ACF diagnostics](figures/diagnostics_acf.png)

![Student-t QQ](figures/tail_qq_student_t.png)

## Fitted generative model

**GARCH(1,1) + standardized Student-t innovations**

| Parameter | Estimate |
|---|---:|
| mu | {_fmt(params.mu)} |
| omega | {_fmt(params.omega)} |
| alpha | {_fmt(params.alpha)} |
| beta | {_fmt(params.beta)} |
| alpha + beta | {_fmt(params.persistence)} |
| Student-t nu | {_fmt(params.nu)} |

The simulation interface is explicitly fit-then-simulate and uses a fixed random seed. Student-t draws are standardized to unit variance before entering the GARCH recursion.

## Validation gates

These are pragmatic model acceptance gates rather than formal significance levels. Central-distribution and volatility targets have tighter tolerances; 99% tail measures and drawdowns are looser because their effective sample sizes are smaller.

| Metric | Real | Synthetic | Error | Threshold | Status |
|---|---:|---:|---:|---:|:---:|
{chr(10).join(rows)}

![Marginal comparison](figures/marginal_comparison.png)

![Squared ACF comparison](figures/squared_acf_real_vs_synthetic.png)

![Drawdown comparison](figures/drawdown_distribution.png)

## Failure mode and next step

{failure_discussion}

## What this validation does and does not establish

This is primarily a **generative calibration / posterior-predictive-style check**: after fitting the historical process, it asks whether simulated scenarios reproduce selected properties of that process. It does **not** establish out-of-sample forecasting skill or prove adequacy for future regimes.

A stronger production validation would add rolling-origin or regime holdouts, parameter stability checks, stress-period analysis, and explicit model-risk governance.
"""
    output_path.write_text(text, encoding="utf-8")
