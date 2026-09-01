from __future__ import annotations

from pathlib import Path

from .challenger import GjrSkewTParams
from .diagnostics import DiagnosticSummary
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
        f"the mean absolute squared-return ACF is {_fmt(summary.mean_abs_squared_acf)}, evidence "
        f"of conditional heteroskedasticity / volatility clustering. That motivates a "
        f"volatility-aware generator rather than iid Monte Carlo.\n\n"
        f"A parsimonious GARCH(1,1)-Student-t model was used first as a development baseline. "
        f"Its validation exposed a material asymmetry miss: the historical returns are clearly "
        f"negatively skewed, while a symmetric innovation model cannot reproduce that feature "
        f"reliably. I therefore made one targeted refinement rather than escalating to a neural "
        f"generator: **GJR-GARCH(1,1,1) with Hansen skewed-t innovations**. GJR allows negative "
        f"and positive shocks to affect future volatility differently, while skewed-t innovations "
        f"retain heavy tails and permit conditional asymmetry."
    )


def write_report(
    output_path: Path,
    summary: DiagnosticSummary,
    params: GjrSkewTParams,
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
    fail_text = "\n".join(
        f"- **{g.metric}** fails its declared gate; I retain the failure rather than "
        f"retuning the threshold after seeing the result."
        for g in failures
    )
    if not fail_text:
        fail_text = "- No declared gate fails on this particular seeded run."

    text = f"""# Brent synthetic-scenario validation report

## Scope

Daily Brent crude `BZ=F` close prices are fetched in code from Yahoo Finance. The analysis uses percentage log returns and a fixed historical cut-off for reproducibility. The submitted generator is a **GJR-GARCH(1,1,1) with Hansen skewed-t innovations**.

## Statistical diagnostics and model choice

{interpretation(summary)}

![ACF diagnostics](figures/diagnostics_acf.png)

![Student-t QQ](figures/tail_qq_student_t.png)

## Fitted generative model

**GJR-GARCH(1,1,1) + Hansen skewed-t innovations**

| Parameter | Estimate |
|---|---:|
| mu | {_fmt(params.mu)} |
| omega | {_fmt(params.omega)} |
| alpha | {_fmt(params.alpha)} |
| gamma (negative-shock leverage) | {_fmt(params.gamma)} |
| beta | {_fmt(params.beta)} |
| alpha + gamma/2 + beta (approx.) | {_fmt(params.approximate_persistence)} |
| skew-t eta | {_fmt(params.eta)} |
| skew-t lambda | {_fmt(params.lam)} |

The fit-then-simulate interface is explicit and every stochastic source is seed-controlled. Simulation starts each independent path from a sampled historical fitted residual/conditional-variance state, so the calibration check represents a mixture of empirically observed calm and stressed starting conditions rather than forcing all paths into one arbitrary initial volatility state.

The baseline and the selected model are compared in `reports/model_comparison.md`. A separate 10-seed check in `reports/robustness_report.md` is used to distinguish structural behaviour from one favourable Monte Carlo realization. Model selection is therefore not based on a single seed or a raw count of green gates alone.

## Validation gates

These are pragmatic engineering acceptance gates, not formal hypothesis-test significance levels. Central-distribution and volatility targets have tighter tolerances; far-tail measures and drawdowns are looser because their effective sample sizes are smaller.

| Metric | Real | Synthetic | Error | Threshold | Status |
|---|---:|---:|---:|---:|:---:|
{chr(10).join(rows)}

![Marginal comparison](figures/marginal_comparison.png)

![Squared ACF comparison](figures/squared_acf_real_vs_synthetic.png)

![Drawdown comparison](figures/drawdown_distribution.png)

## Honest failure mode

{fail_text}

The important remaining model-risk issue is the **ultra-tail / higher-moment behaviour**. Both the development baseline and the asymmetric challenger can generate very large sample kurtosis in finite simulations; the multi-seed report makes that instability visible rather than hiding it behind one realization. The models also leave residual mismatch in squared-return autocorrelation, indicating that a single stationary volatility recursion does not capture every feature of the historical volatility process.

I would not address those failures by adding complexity indiscriminately. My next experiment would depend on the production objective: **GARCH-EVT** (POT/GPD on standardized residual tails) if conditional tail calibration is the priority, or a **regime-aware volatility model** if persistence and stress-state transitions remain the dominant failure. Either extension would be validated on regime/rolling holdouts before production use.

## What this validation does and does not establish

This is primarily a **generative calibration / posterior-predictive-style check**: after fitting the historical process, it asks whether simulated scenarios reproduce selected properties of that process. It does **not** establish out-of-sample forecasting skill, causal geopolitical understanding, or adequacy for genuinely unprecedented future regimes.

A production validation programme would add rolling-origin and regime holdouts, parameter-stability monitoring, explicit stress-period tests, sensitivity to the futures-series construction, and model-risk governance. `BZ=F` is a convenient front-month proxy, not a professionally engineered constant-maturity Brent series; roll and contract-construction effects are therefore a known data limitation.
"""
    output_path.write_text(text, encoding="utf-8")
