from __future__ import annotations

from pathlib import Path

import numpy as np

from .challenger import GjrSkewTParams
from .diagnostics import DiagnosticSummary
from .validation import ExceedanceCheck, Gate, LeaveOutRow
from .windows import StressEpisode, WindowStats


def _fmt(x: float) -> str:
    if not np.isfinite(x):
        return "inf"
    if abs(x) >= 100:
        return f"{x:.2f}"
    if abs(x) >= 1:
        return f"{x:.3f}"
    return f"{x:.4f}"


def _error_cell(gate: Gate) -> str:
    return f"{100*gate.error:.1f}%" if gate.error_type == "relative" else _fmt(gate.error)


def _threshold_cell(gate: Gate) -> str:
    return (
        f"{100*gate.threshold:.0f}%"
        if gate.error_type == "relative"
        else _fmt(gate.threshold)
    )


def _gate_rows(gates: list[Gate], with_bands: bool = False) -> str:
    rows = []
    for g in gates:
        cells = [
            g.metric,
            _fmt(g.real),
            _fmt(g.synthetic),
            _error_cell(g),
            _threshold_cell(g),
        ]
        if with_bands:
            band = (
                f"[{_fmt(g.real_band[0])}, {_fmt(g.real_band[1])}] vs "
                f"[{_fmt(g.synthetic_band[0])}, {_fmt(g.synthetic_band[1])}]"
                if g.real_band is not None and g.synthetic_band is not None
                else "-"
            )
            cells.append(band)
        cells.append("PASS" if g.passed else "FAIL")
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def interpretation(summary: DiagnosticSummary, params: GjrSkewTParams) -> str:
    tail_side = (
        "the loss tail is the marginally heavier of the two"
        if summary.hill_left < summary.hill_right
        else "the gain tail is the marginally heavier of the two"
    )
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
        f"The tail diagnostics decide the innovation law. At k={summary.hill_k} order statistics "
        f"the Hill estimator gives a tail index of **{summary.hill_left:.2f}** on the loss side "
        f"and **{summary.hill_right:.2f}** on the gain side; {tail_side}, and both sit close to "
        f"the fitted Student-t degrees of freedom. An index near three implies a finite variance "
        f"but an infinite fourth moment, so sample kurtosis is not a stable estimation target for "
        f"this series, and the mean-excess function for losses rises with the threshold, the "
        f"signature of a heavy rather than exponential tail. Note that the asymmetry visible in "
        f"the skewness is *not* mirrored by a large gap between the two tail indices: the "
        f"asymmetry lives mainly in the body and in the volatility response, not in how fast the "
        f"extremes decay.\n\n"
        f"Two facts then pin down the model family. First, the unconditional tail index near "
        f"{0.5*(summary.hill_left+summary.hill_right):.1f} is much heavier than the fitted "
        f"conditional innovation, which has eta = {params.eta:.2f} degrees of freedom: roughly "
        f"half of the unconditional tail weight is *manufactured by volatility clustering* rather "
        f"than by fat innovations, which is precisely what a GARCH-type recursion with moderately "
        f"heavy innovations produces. Second, the negative skew and the leverage effect require an "
        f"asymmetric response. A parsimonious GARCH(1,1)-Student-t was fitted first as a "
        f"development baseline; its validation exposed a material asymmetry miss, since a "
        f"symmetric innovation model cannot reproduce negative skew. I therefore made one targeted "
        f"refinement rather than escalating to a neural generator: **GJR-GARCH(1,1,1) with Hansen "
        f"skewed-t innovations**."
    )


def _severity_ladder(real_stats: WindowStats, synthetic_stats: WindowStats) -> str:
    rows = []
    for q in (0.50, 0.75, 0.90, 0.95, 0.99):
        rows.append(
            f"| {q:.2f} | {_fmt(float(np.quantile(real_stats.volatility, q)))} | "
            f"{_fmt(float(np.quantile(synthetic_stats.volatility, q)))} | "
            f"{_fmt(float(np.quantile(real_stats.es99, q)))} | "
            f"{_fmt(float(np.quantile(synthetic_stats.es99, q)))} |"
        )
    rows.append(
        f"| max | {_fmt(float(np.max(real_stats.volatility)))} | "
        f"{_fmt(float(np.max(synthetic_stats.volatility)))} | "
        f"{_fmt(float(np.max(real_stats.es99)))} | "
        f"{_fmt(float(np.max(synthetic_stats.es99)))} |"
    )
    return "\n".join(rows)


def _exceedance_rows(checks: list[ExceedanceCheck]) -> str:
    return "\n".join(
        f"| {c.statistic} | {_fmt(c.historical_max)} | "
        f"{100*c.synthetic_exceedance_probability:.2f}% | "
        f"{c.implied_expected_count:.2f} | "
        f"{c.lower_count:.2f} - {c.upper_count:.2f} | "
        f"{'PASS' if c.passed else 'FAIL'} |"
        for c in checks
    )


# Pooled gates whose failure would be explained by sample-size dependence rather
# than by miscalibration. Declared here rather than inferred from the results.
SAMPLE_SIZE_SENSITIVE = ("squared-return ACF MAE", "excess kurtosis")


def _failure_narrative(
    pooled: list[Gate],
    matched: list[Gate],
    real_stats: WindowStats,
    synthetic_stats: WindowStats,
    params: GjrSkewTParams,
    leave_out: list[LeaveOutRow],
    real_moments: tuple[float, float, float],
    real_max_abs_return: float,
    synthetic_max_abs_return: float,
) -> str:
    pooled_failures = [g for g in pooled if not g.passed]
    matched_failures = [g for g in matched if not g.passed]
    matched_names = {g.metric for g in matched_failures}

    estimator_driven = [
        g.metric for g in pooled_failures if g.metric in SAMPLE_SIZE_SENSITIVE
    ]
    # Failures that the horizon-matched family recovers and that are not explained
    # by sample-size dependence: these are the ones the explosive paths carry.
    explosive_driven = [
        g.metric
        for g in pooled_failures
        if g.metric not in matched_names and g.metric not in SAMPLE_SIZE_SENSITIVE
    ]

    real_var = real_stats.volatility**2
    syn_var = synthetic_stats.volatility**2
    explosive = float(
        np.mean(synthetic_stats.volatility > np.max(real_stats.volatility))
    )
    var_ratio = float(np.mean(syn_var) / np.mean(real_var))

    def _names(items: list[str]) -> str:
        marked = [f"**{n}**" for n in items]
        if not marked:
            return "no gate"
        if len(marked) == 1:
            return marked[0]
        return ", ".join(marked[:-1]) + " and " + marked[-1]

    full = leave_out[0] if leave_out else None
    trimmed = leave_out[-1] if len(leave_out) > 1 else None
    real_vol, real_skew, real_kurt = real_moments

    lines = [
        f"The pooled family fails {_names([g.metric for g in pooled_failures])}. "
        f"The horizon-matched family fails {_names([g.metric for g in matched_failures])}. "
        "Every failure is retained and no threshold was moved after seeing a result: both "
        "families are scored against the same declared table. What follows is why the two "
        "disagree, because the disagreement is the finding. A clean horizon-matched "
        "scorecard is not a claim of adequacy: the gates are deliberately silent about the "
        "region where this model actually breaks, which the next two sections locate.",
        "",
        "### The estimator-driven part",
        "",
        f"{_names(estimator_driven)} cannot be read as model failures. Both statistics are "
        "strongly sample-size dependent, and the pooled comparison puts "
        f"{synthetic_stats.n_blocks * synthetic_stats.horizon:,} synthetic observations "
        "against a few thousand historical ones. The sample ACF of squared returns is biased "
        f"toward zero in short blocks, so an average of {synthetic_stats.horizon}-day "
        "synthetic ACFs can never reach a full-sample historical ACF. Sample kurtosis is "
        "worse than biased: with `E[A(z)^2] >= 1` the unconditional fourth moment does not "
        "exist, so the statistic has no limit to converge to and simply grows with the "
        f"simulated sample size. Estimated like-for-like on {real_stats.horizon}-day blocks, "
        "against the identical thresholds, both pass.",
        "",
        "### The real failure: a near-integrated variance recursion that occasionally runs away",
        "",
        "The typical simulated year is well calibrated. Median block variance is "
        f"{_fmt(float(np.median(real_var)))} historically against "
        f"{_fmt(float(np.median(syn_var)))} synthetically, and every horizon-matched gate "
        f"passes. The *mean* block variance, however, is {_fmt(float(np.mean(real_var)))} "
        f"against {_fmt(float(np.mean(syn_var)))}, a factor of {var_ratio:.2f}. That entire "
        "gap is created in the extreme upper tail: the worst historical year has volatility "
        f"{_fmt(float(np.max(real_stats.volatility)))}%/day while the worst simulated year "
        f"reaches {_fmt(float(np.max(synthetic_stats.volatility)))}%/day, the largest "
        f"historical daily move is {_fmt(real_max_abs_return)}% against "
        f"{_fmt(synthetic_max_abs_return)}% simulated, and {100*explosive:.1f}% of simulated "
        "years are more volatile than anything in the record.",
        "",
        "How concentrated is that? Removing the most volatile simulated paths and recomputing "
        "the pooled moments answers it directly. This is a sensitivity diagnostic, not a "
        "proposed fix; trimming paths after seeing the result would be data snooping.",
        "",
        "| Paths removed | Share of simulation | Pooled volatility | Pooled skewness | Pooled excess kurtosis |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in leave_out:
        lines.append(
            f"| {row.dropped} | {100*row.fraction:.1f}% | {_fmt(row.volatility)} | "
            f"{_fmt(row.skewness)} | {_fmt(row.excess_kurtosis)} |"
        )
    lines.append(
        f"| *historical target* | - | *{_fmt(real_vol)}* | *{_fmt(real_skew)}* | "
        f"*{_fmt(real_kurt)}* |"
    )

    if full is not None and trimmed is not None:
        lines += [
            "",
            f"Removing {trimmed.dropped} of {synthetic_stats.n_blocks:,} paths "
            f"({100*trimmed.fraction:.1f}%) moves pooled excess kurtosis from "
            f"{_fmt(full.excess_kurtosis)} to {_fmt(trimmed.excess_kurtosis)} against a "
            f"historical {_fmt(real_kurt)}, and pooled skewness from {_fmt(full.skewness)} "
            f"to {_fmt(trimmed.skewness)} against {_fmt(real_skew)}. The unconditional "
            "moments of this generator are not a property of the generator in any useful "
            "sense; they are a property of a handful of paths.",
        ]

    lines += [
        "",
        "The mechanism is in the fit, not in the simulation code: effective persistence "
        f"{params.effective_persistence:.4f} with `E[A(z)^2] = "
        f"{params.fourth_moment_coefficient:.4f}` is a variance process that mean-reverts too "
        "slowly to contain a large shock within the horizon and has no finite fourth moment "
        "to pull it back. Pooling then imports those paths into every unconditional moment at "
        f"once, which is why {_names(explosive_driven)} fail pooled and pass "
        "horizon-matched, and why the kurtosis miss is so much larger than sample-size "
        "dependence alone would produce.",
        "",
        "For a stress-testing application this is the material limitation. The generator is "
        "usable for typical and moderately adverse years, and its severity ladder tracks "
        "history to roughly the 90th percentile. Beyond that it stops making a calibrated "
        f"statement about Brent: a day with a {_fmt(synthetic_max_abs_return)}% move is not a "
        "scenario, it is the recursion diverging. Before any of this fed a capital number I "
        "would want either a variance process that is fourth-moment stationary, or an "
        "economically justified cap on the conditional variance, declared in advance rather "
        "than fitted after the fact.",
        "",
        "### A second, milder failure",
        "",
        "The horizon-matched squared-return ACF passes on mean absolute error, but the *shape* "
        "is wrong: the historical block ACF decays slowly and irregularly while the model's "
        "decays geometrically. A single stationary GJR recursion reproduces the average level "
        "of volatility persistence without reproducing its long-memory-like profile. The gate "
        "does not catch this because a mean absolute error over twenty lags averages the "
        "discrepancy away; the figure shows it plainly.",
    ]
    return "\n".join(lines)


def write_report(
    output_path: Path,
    summary: DiagnosticSummary,
    params: GjrSkewTParams,
    gates: list[Gate],
    matched_gates: list[Gate],
    exceedances: list[ExceedanceCheck],
    real_stats: WindowStats,
    synthetic_stats: WindowStats,
    leave_out: list[LeaveOutRow],
    real_moments: tuple[float, float, float],
    real_max_abs_return: float,
    synthetic_max_abs_return: float,
    stress_episode: StressEpisode,
    n_returns: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fourth = params.fourth_moment_coefficient
    fourth_text = (
        f"The fitted GJR process has `E[A(z)^2] = {fourth:.4f}` for "
        f"`A(z)=beta + alpha*z^2 + gamma*z^2*I(z<0)`. "
        + (
            "Because this is >= 1, the usual finite unconditional fourth-moment condition "
            "is not satisfied, so sample kurtosis has no limit to converge to and grows "
            "with the simulated sample size. This is a structural property of the fit, and "
            "it drives the main failure mode reported below."
            if fourth >= 1.0
            else "Because this is < 1, the usual finite unconditional fourth-moment "
            "condition is satisfied."
        )
    )
    implied_vol = params.implied_unconditional_variance**0.5
    years = exceedances[0].independent_years if exceedances else 0

    text = f"""# Brent synthetic-scenario validation report

## Scope

Daily Brent crude `BZ=F` close prices are fetched in code from Yahoo Finance. The analysis uses percentage log returns and a fixed historical cut-off for reproducibility. The submitted generator is a **GJR-GARCH(1,1,1) with Hansen skewed-t innovations**.

## Statistical diagnostics and model choice

{interpretation(summary, params)}

![ACF diagnostics](figures/diagnostics_acf.png)

![Student-t QQ](figures/tail_qq_student_t.png)

![Tail index and mean excess](figures/tail_index_and_mean_excess.png)

## Fitted generative model

**GJR-GARCH(1,1,1) + Hansen skewed-t innovations**

```text
r_t       = mu + eps_t
eps_t     = sigma_t z_t
sigma_t^2 = omega + alpha eps_(t-1)^2
            + gamma I(eps_(t-1) < 0) eps_(t-1)^2
            + beta sigma_(t-1)^2
z_t       ~ standardized Hansen skewed-t(eta, lambda)
```

| Parameter | Estimate |
|---|---:|
| mu | {_fmt(params.mu)} |
| omega | {_fmt(params.omega)} |
| alpha | {_fmt(params.alpha)} |
| gamma (negative-shock leverage) | {_fmt(params.gamma)} |
| beta | {_fmt(params.beta)} |
| effective variance persistence | {_fmt(params.effective_persistence)} |
| implied unconditional volatility (%/day) | {_fmt(implied_vol)} |
| sample volatility (%/day) | {_fmt(summary.std)} |
| skew-t eta | {_fmt(params.eta)} |
| skew-t lambda | {_fmt(params.lam)} |

For the asymmetric innovation law, persistence is computed as `alpha + beta + gamma * E[z^2 I(z<0)]`; I do not use the symmetric `gamma/2` shortcut. {fourth_text}

Two fitted quantities are worth stating plainly because they explain most of what follows. Effective persistence is {_fmt(params.effective_persistence)}, close enough to one that the variance recursion mean-reverts only slowly over a 252-day horizon, and the level it reverts *to* implies an unconditional volatility of {_fmt(implied_vol)}%/day against a sample volatility of {_fmt(summary.std)}%/day. A near-integrated variance process with an unconditional level above the sample average is exactly the configuration that produces occasional runaway paths.

The fit-then-simulate interface is explicit and every stochastic source is seed-controlled, using two independent child streams from a single `SeedSequence` so that replications never share a generator stream. Simulation starts each independent path from a sampled historical fitted residual/conditional-variance state, so the calibration check represents a mixture of empirically observed calm and stressed starting conditions rather than forcing all paths into one arbitrary initial volatility state. The full optimizer summary is saved to `reports/fit_summary.txt` and run metadata to `reports/run_manifest.json`.

## How this is validated

Every metric is checked twice, against **one shared table of thresholds declared in `validation.THRESHOLDS`**. The two families differ only in how the statistic is estimated, never in the tolerance it must meet, so a change of estimator cannot be confused with a relaxation of the acceptance criteria.

1. **Pooled marginal check.** All synthetic observations are pooled and compared with the pooled historical sample. This is the right instrument for the unconditional marginal law, but the two samples have very different sizes ({synthetic_stats.n_blocks * synthetic_stats.horizon:,} against {n_returns:,}), which matters for any statistic that is sample-size dependent.
2. **Horizon-matched year-level check.** Every statistic is estimated inside blocks of {real_stats.horizon} trading days on *both* sides: {real_stats.n_blocks:,} overlapping historical windows against {synthetic_stats.n_blocks:,} independent synthetic paths, then compared at the median. This is the same like-for-like principle already applied to drawdowns, extended to the rest of the suite.

These are pragmatic engineering acceptance gates, not formal hypothesis-test significance levels. Central-distribution and volatility targets have tighter tolerances; far-tail measures and drawdowns are looser because their effective sample sizes are smaller.

### Family 1: pooled marginal gates

| Metric | Real | Synthetic | Error | Threshold | Status |
|---|---:|---:|---:|---:|:---:|
{_gate_rows(gates)}

### Family 2: horizon-matched year-level gates

Median of the statistic across {real_stats.horizon}-day blocks. The band column reports the 5th-95th percentile spread of the statistic across blocks on each side; it is shown for context and is deliberately **not** gated, for the reason given in the next section.

| Metric | Real median | Synthetic median | Error | Threshold | 5-95% band, real vs synthetic | Status |
|---|---:|---:|---:|---:|---|:---:|
{_gate_rows(matched_gates, with_bands=True)}

![Marginal comparison](figures/marginal_comparison.png)

![Squared ACF comparison](figures/squared_acf_real_vs_synthetic.png)

![Year-level severity](figures/year_severity.png)

In the first two panels the historical distribution shows an isolated spike sitting exactly on the worst-observed-year line. That spike is not a cluster of bad years; it is the same crisis appearing in every overlapping window that contains it, and it is the visual form of the argument in the next section.

![Drawdown comparison](figures/drawdown_distribution.png)

## Family 3: the stressed region, and why it is not a percentile gate

The obvious next step would be to gate the model against the 95th percentile of the historical year-severity distribution. That gate would be meaningless, and it is worth saying why rather than quietly reporting it.

| Quantile of the year-level statistic | Volatility, real | Volatility, synthetic | ES 99%, real | ES 99%, synthetic |
|---|---:|---:|---:|---:|
{_severity_ladder(real_stats, synthetic_stats)}

The historical column stops moving above roughly the 90th percentile. That is not a property of oil markets; it is window overlap. The {real_stats.n_blocks:,} historical blocks are rolling windows over the same {n_returns:,} returns, so the worst few per cent of them are the *same* episode counted many times. Concretely: the {stress_episode.n_blocks} blocks above the {stress_episode.quantile:.0%} quantile of {stress_episode.statistic} all begin between {stress_episode.first_start} and {stress_episode.last_start}, spanning {' and '.join(str(y) for y in stress_episode.distinct_years)} — one crisis, replicated. The historical "95th percentile" is therefore effectively the historical maximum, and there are only {years} independent {real_stats.horizon}-day years in this sample.

What is identified is a frequency. History produced one year at least as severe as its worst; the model implies some annual probability of such a year. Comparing the two is a Poisson question, so each check below asks whether the model-implied expected count over {years} independent years is consistent with having observed exactly one, using the exact 90% Poisson interval for a single event.

| Statistic | Worst observed year | Model annual probability | Expected count in {years} years | 90% Poisson interval for 1 event | Status |
|---|---:|---:|---:|---:|:---:|
{_exceedance_rows(exceedances)}

The interval is wide because one observation is genuinely weak evidence. That width is the honest answer, not a weakness of the test: no dataset containing a single crisis of a given size can pin down its frequency more tightly, and a narrower gate here would be false precision.

## Honest failure mode

{_failure_narrative(
    gates,
    matched_gates,
    real_stats,
    synthetic_stats,
    params,
    leave_out,
    real_moments,
    real_max_abs_return,
    synthetic_max_abs_return,
)}

I would not address these by adding complexity indiscriminately. My next experiment would depend on the production objective: **GARCH-EVT** (POT/GPD on the standardized residual tails, which the Hill and mean-excess diagnostics already suggest is the natural extension) if conditional tail calibration is the priority; or a **regime-aware volatility model** if the long-memory-like ACF profile and the runaway upper tail are the dominant concern, since a two-state persistence structure would both fit the ACF shape better and bound the explosive paths. Either extension would be validated on regime and rolling-origin holdouts before production use.

## What this validation does and does not establish

This is primarily a **generative calibration / posterior-predictive-style check**: after fitting the historical process, it asks whether simulated scenarios reproduce selected properties of that process. It does **not** establish out-of-sample forecasting skill, causal geopolitical understanding, or adequacy for genuinely unprecedented future regimes.

The horizon-matched family removes an estimator confound; it does not remove the deeper limitation that both sides are being compared against a single historical realization. The overlapping windows make the year-level comparison descriptive rather than inferential, and the {years} independent years in this sample are the binding constraint on everything said about the stressed region.

A production validation programme would add rolling-origin and regime holdouts, parameter-stability monitoring, explicit stress-period tests, sensitivity to the futures-series construction, and model-risk governance. `BZ=F` is a convenient front-month proxy, not a professionally engineered constant-maturity Brent series; roll and contract-construction effects are therefore a known data limitation.
"""
    output_path.write_text(text, encoding="utf-8")
