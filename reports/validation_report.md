# Brent synthetic-scenario validation report

## Scope

Daily Brent crude `BZ=F` close prices are fetched in code from Yahoo Finance. The analysis uses percentage log returns and a fixed historical cut-off for reproducibility. The submitted generator is a **GJR-GARCH(1,1,1) with Hansen skewed-t innovations**.

## Statistical diagnostics and model choice

The sample contains **4,158** daily observations. Mean daily log return is 0.0029% with volatility 2.344%. The distribution is negatively skewed (-0.9584) and has excess kurtosis 14.691, which is inconsistent with a thin-tailed Gaussian description. A fitted Student-t reference has approximately **2.97 degrees of freedom**, providing a compact heavy-tailed benchmark for the QQ diagnostic.

Linear return dependence is comparatively limited: the maximum absolute return ACF over the inspected non-zero lags is 0.0503. In contrast, the mean absolute squared-return ACF is 0.1271, evidence of conditional heteroskedasticity / volatility clustering. That motivates a volatility-aware generator rather than iid Monte Carlo.

The tail diagnostics decide the innovation law. At k=100 order statistics the Hill estimator gives a tail index of **2.94** on the loss side and **2.91** on the gain side; the gain tail is the marginally heavier of the two, and both sit close to the fitted Student-t degrees of freedom. An index near three implies a finite variance but an infinite fourth moment, so sample kurtosis is not a stable estimation target for this series, and the mean-excess function for losses rises with the threshold, the signature of a heavy rather than exponential tail. Note that the asymmetry visible in the skewness is *not* mirrored by a large gap between the two tail indices: the asymmetry lives mainly in the body and in the volatility response, not in how fast the extremes decay.

Two facts then pin down the model family. First, the unconditional tail index near 2.9 is much heavier than the fitted conditional innovation, which has eta = 5.29 degrees of freedom: roughly half of the unconditional tail weight is *manufactured by volatility clustering* rather than by fat innovations, which is precisely what a GARCH-type recursion with moderately heavy innovations produces. Second, the negative skew and the leverage effect require an asymmetric response. A parsimonious GARCH(1,1)-Student-t was fitted first as a development baseline; its validation exposed a material asymmetry miss, since a symmetric innovation model cannot reproduce negative skew. I therefore made one targeted refinement rather than escalating to a neural generator: **GJR-GARCH(1,1,1) with Hansen skewed-t innovations**.

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
| mu | -0.0003 |
| omega | 0.0564 |
| alpha | 0.0653 |
| gamma (negative-shock leverage) | 0.0344 |
| beta | 0.9092 |
| effective variance persistence | 0.9935 |
| implied unconditional volatility (%/day) | 2.936 |
| sample volatility (%/day) | 2.344 |
| skew-t eta | 5.293 |
| skew-t lambda | -0.1215 |

For the asymmetric innovation law, persistence is computed as `alpha + beta + gamma * E[z^2 I(z<0)]`; I do not use the symmetric `gamma/2` shortcut. The fitted GJR process has `E[A(z)^2] = 1.0457` for `A(z)=beta + alpha*z^2 + gamma*z^2*I(z<0)`. Because this is >= 1, the usual finite unconditional fourth-moment condition is not satisfied, so sample kurtosis has no limit to converge to and grows with the simulated sample size. This is a structural property of the fit, and it drives the main failure mode reported below.

Two fitted quantities are worth stating plainly because they explain most of what follows. Effective persistence is 0.9935, close enough to one that the variance recursion mean-reverts only slowly over a 252-day horizon, and the level it reverts *to* implies an unconditional volatility of 2.936%/day against a sample volatility of 2.344%/day. A near-integrated variance process with an unconditional level above the sample average is exactly the configuration that produces occasional runaway paths.

The fit-then-simulate interface is explicit and every stochastic source is seed-controlled, using two independent child streams from a single `SeedSequence` so that replications never share a generator stream. Simulation starts each independent path from a sampled historical fitted residual/conditional-variance state, so the calibration check represents a mixture of empirically observed calm and stressed starting conditions rather than forcing all paths into one arbitrary initial volatility state. The full optimizer summary is saved to `reports/fit_summary.txt` and run metadata to `reports/run_manifest.json`.

## How this is validated

Every metric is checked twice, against **one shared table of thresholds declared in `validation.THRESHOLDS`**. The two families differ only in how the statistic is estimated, never in the tolerance it must meet, so a change of estimator cannot be confused with a relaxation of the acceptance criteria.

1. **Pooled marginal check.** All synthetic observations are pooled and compared with the pooled historical sample. This is the right instrument for the unconditional marginal law, but the two samples have very different sizes (252,000 against 4,158), which matters for any statistic that is sample-size dependent.
2. **Horizon-matched year-level check.** Every statistic is estimated inside blocks of 252 trading days on *both* sides: 3,907 overlapping historical windows against 1,000 independent synthetic paths, then compared at the median. This is the same like-for-like principle already applied to drawdowns, extended to the rest of the suite.

These are pragmatic engineering acceptance gates, not formal hypothesis-test significance levels. Central-distribution and volatility targets have tighter tolerances; far-tail measures and drawdowns are looser because their effective sample sizes are smaller.

### Family 1: pooled marginal gates

| Metric | Real | Synthetic | Error | Threshold | Status |
|---|---:|---:|---:|---:|:---:|
| mean return (pp) | 0.0029 | -0.0043 | 0.0072 | 0.1000 | PASS |
| volatility | 2.344 | 2.827 | 20.6% | 10% | FAIL |
| skewness | -0.9584 | -4.876 | 3.918 | 0.5000 | FAIL |
| excess kurtosis | 14.691 | 389.49 | 374.80 | 2.000 | FAIL |
| q01 | -6.707 | -7.534 | 12.3% | 20% | PASS |
| q05 | -3.639 | -3.670 | 0.8% | 15% | PASS |
| q95 | 3.244 | 3.381 | 4.2% | 15% | PASS |
| q99 | 5.955 | 6.750 | 13.3% | 20% | PASS |
| VaR 95% | 3.639 | 3.670 | 0.8% | 15% | PASS |
| ES 95% | 5.756 | 6.504 | 13.0% | 20% | PASS |
| VaR 99% | 6.707 | 7.534 | 12.3% | 20% | PASS |
| ES 99% | 9.939 | 12.696 | 27.7% | 25% | FAIL |
| squared-return ACF MAE | 0.0000 | 0.0649 | 0.0649 | 0.0500 | FAIL |
| drawdown median | 0.2459 | 0.2848 | 15.8% | 25% | PASS |
| drawdown p95 | 0.6951 | 0.6189 | 11.0% | 30% | PASS |

### Family 2: horizon-matched year-level gates

Median of the statistic across 252-day blocks. The band column reports the 5th-95th percentile spread of the statistic across blocks on each side; it is shown for context and is deliberately **not** gated, for the reason given in the next section.

| Metric | Real median | Synthetic median | Error | Threshold | 5-95% band, real vs synthetic | Status |
|---|---:|---:|---:|---:|---|:---:|
| mean return (pp) | -0.0055 | 0.0124 | 0.0179 | 0.1000 | [-0.2385, 0.2085] vs [-0.2425, 0.2239] | PASS |
| volatility | 1.966 | 1.929 | 1.9% | 10% | [1.062, 4.404] vs [1.278, 4.432] | PASS |
| skewness | -0.4525 | -0.2696 | 0.1829 | 0.5000 | [-1.429, 0.3519] vs [-1.091, 0.4946] | PASS |
| excess kurtosis | 2.132 | 2.291 | 0.1591 | 2.000 | [0.5412, 12.947] vs [0.6455, 8.233] | PASS |
| q01 | -5.636 | -5.298 | 6.0% | 20% | [-13.123, -2.746] vs [-13.078, -3.219] | PASS |
| q05 | -3.341 | -3.147 | 5.8% | 15% | [-6.079, -1.779] vs [-7.280, -2.019] | PASS |
| q95 | 2.848 | 2.892 | 1.6% | 15% | [1.625, 5.117] vs [1.953, 6.564] | PASS |
| q99 | 4.274 | 4.742 | 11.0% | 20% | [2.478, 13.009] vs [2.909, 11.407] | PASS |
| VaR 95% | 3.341 | 3.147 | 5.8% | 15% | [1.779, 6.079] vs [2.019, 7.280] | PASS |
| ES 95% | 4.639 | 4.539 | 2.2% | 20% | [2.445, 11.729] vs [2.804, 10.929] | PASS |
| VaR 99% | 5.636 | 5.298 | 6.0% | 20% | [2.746, 13.123] vs [3.219, 13.078] | PASS |
| ES 99% | 6.889 | 6.516 | 5.4% | 25% | [3.288, 23.313] vs [3.679, 17.807] | PASS |
| squared-return ACF MAE | 0.0000 | 0.0191 | 0.0191 | 0.0500 | - | PASS |
| drawdown median | 0.2459 | 0.2848 | 15.8% | 25% | - | PASS |
| drawdown p95 | 0.6951 | 0.6189 | 11.0% | 30% | - | PASS |

![Marginal comparison](figures/marginal_comparison.png)

![Squared ACF comparison](figures/squared_acf_real_vs_synthetic.png)

![Year-level severity](figures/year_severity.png)

In the first two panels the historical distribution shows an isolated spike sitting exactly on the worst-observed-year line. That spike is not a cluster of bad years; it is the same crisis appearing in every overlapping window that contains it, and it is the visual form of the argument in the next section.

![Drawdown comparison](figures/drawdown_distribution.png)

## Family 3: the stressed region, and why it is not a percentile gate

The obvious next step would be to gate the model against the 95th percentile of the historical year-severity distribution. That gate would be meaningless, and it is worth saying why rather than quietly reporting it.

| Quantile of the year-level statistic | Volatility, real | Volatility, synthetic | ES 99%, real | ES 99%, synthetic |
|---|---:|---:|---:|---:|
| 0.50 | 1.966 | 1.929 | 6.889 | 6.516 |
| 0.75 | 2.582 | 2.504 | 8.383 | 8.993 |
| 0.90 | 3.008 | 3.407 | 11.978 | 12.942 |
| 0.95 | 4.404 | 4.432 | 23.313 | 17.807 |
| 0.99 | 4.502 | 7.166 | 23.313 | 30.671 |
| max | 4.537 | 36.645 | 23.313 | 184.57 |

The historical column stops moving above roughly the 90th percentile. That is not a property of oil markets; it is window overlap. The 3,907 historical blocks are rolling windows over the same 4,158 returns, so the worst few per cent of them are the *same* episode counted many times. Concretely: the 222 blocks above the 95% quantile of ES 99% all begin between 2019-04-23 and 2020-03-09, spanning 2019 and 2020 — one crisis, replicated. The historical "95th percentile" is therefore effectively the historical maximum, and there are only 16 independent 252-day years in this sample.

What is identified is a frequency. History produced one year at least as severe as its worst; the model implies some annual probability of such a year. Comparing the two is a Poisson question, so each check below asks whether the model-implied expected count over 16 independent years is consistent with having observed exactly one, using the exact 90% Poisson interval for a single event.

| Statistic | Worst observed year | Model annual probability | Expected count in 16 years | 90% Poisson interval for 1 event | Status |
|---|---:|---:|---:|---:|:---:|
| volatility | 4.537 | 4.40% | 0.70 | 0.05 - 4.74 | PASS |
| VaR 99% | 13.123 | 4.80% | 0.77 | 0.05 - 4.74 | PASS |
| ES 99% | 23.313 | 2.30% | 0.37 | 0.05 - 4.74 | PASS |
| maximum drawdown | 0.7408 | 2.40% | 0.38 | 0.05 - 4.74 | PASS |

The interval is wide because one observation is genuinely weak evidence. That width is the honest answer, not a weakness of the test: no dataset containing a single crisis of a given size can pin down its frequency more tightly, and a narrower gate here would be false precision.

## Honest failure mode

The pooled family fails **volatility**, **skewness**, **excess kurtosis**, **ES 99%** and **squared-return ACF MAE**. The horizon-matched family fails no gate. Every failure is retained and no threshold was moved after seeing a result: both families are scored against the same declared table. What follows is why the two disagree, because the disagreement is the finding. A clean horizon-matched scorecard is not a claim of adequacy: the gates are deliberately silent about the region where this model actually breaks, which the next two sections locate.

### The estimator-driven part

**excess kurtosis** and **squared-return ACF MAE** cannot be read as model failures. Both statistics are strongly sample-size dependent, and the pooled comparison puts 252,000 synthetic observations against a few thousand historical ones. The sample ACF of squared returns is biased toward zero in short blocks, so an average of 252-day synthetic ACFs can never reach a full-sample historical ACF. Sample kurtosis is worse than biased: with `E[A(z)^2] >= 1` the unconditional fourth moment does not exist, so the statistic has no limit to converge to and simply grows with the simulated sample size. Estimated like-for-like on 252-day blocks, against the identical thresholds, both pass.

### The real failure: a near-integrated variance recursion that occasionally runs away

The typical simulated year is well calibrated. Median block variance is 3.864 historically against 3.722 synthetically, and every horizon-matched gate passes. The *mean* block variance, however, is 5.282 against 7.972, a factor of 1.51. That entire gap is created in the extreme upper tail: the worst historical year has volatility 4.537%/day while the worst simulated year reaches 36.645%/day, the largest historical daily move is 27.976% against 212.87% simulated, and 4.4% of simulated years are more volatile than anything in the record.

How concentrated is that? Removing the most volatile simulated paths and recomputing the pooled moments answers it directly. This is a sensitivity diagnostic, not a proposed fix; trimming paths after seeing the result would be data snooping.

| Paths removed | Share of simulation | Pooled volatility | Pooled skewness | Pooled excess kurtosis |
|---|---:|---:|---:|---:|
| 0 | 0.0% | 2.827 | -4.876 | 389.49 |
| 1 | 0.1% | 2.576 | -1.164 | 37.975 |
| 5 | 0.5% | 2.461 | -0.7311 | 17.149 |
| 10 | 1.0% | 2.398 | -0.5786 | 14.385 |
| *historical target* | - | *2.344* | *-0.9584* | *14.691* |

Removing 10 of 1,000 paths (1.0%) moves pooled excess kurtosis from 389.49 to 14.385 against a historical 14.691, and pooled skewness from -4.876 to -0.5786 against -0.9584. The unconditional moments of this generator are not a property of the generator in any useful sense; they are a property of a handful of paths.

The mechanism is in the fit, not in the simulation code: effective persistence 0.9935 with `E[A(z)^2] = 1.0457` is a variance process that mean-reverts too slowly to contain a large shock within the horizon and has no finite fourth moment to pull it back. Pooling then imports those paths into every unconditional moment at once, which is why **volatility**, **skewness** and **ES 99%** fail pooled and pass horizon-matched, and why the kurtosis miss is so much larger than sample-size dependence alone would produce.

For a stress-testing application this is the material limitation. The generator is usable for typical and moderately adverse years, and its severity ladder tracks history to roughly the 90th percentile. Beyond that it stops making a calibrated statement about Brent: a day with a 212.87% move is not a scenario, it is the recursion diverging. Before any of this fed a capital number I would want either a variance process that is fourth-moment stationary, or an economically justified cap on the conditional variance, declared in advance rather than fitted after the fact.

### A second, milder failure

The horizon-matched squared-return ACF passes on mean absolute error, but the *shape* is wrong: the historical block ACF decays slowly and irregularly while the model's decays geometrically. A single stationary GJR recursion reproduces the average level of volatility persistence without reproducing its long-memory-like profile. The gate does not catch this because a mean absolute error over twenty lags averages the discrepancy away; the figure shows it plainly.

I would not address these by adding complexity indiscriminately. My next experiment would depend on the production objective: **GARCH-EVT** (POT/GPD on the standardized residual tails, which the Hill and mean-excess diagnostics already suggest is the natural extension) if conditional tail calibration is the priority; or a **regime-aware volatility model** if the long-memory-like ACF profile and the runaway upper tail are the dominant concern, since a two-state persistence structure would both fit the ACF shape better and bound the explosive paths. Either extension would be validated on regime and rolling-origin holdouts before production use.

## What this validation does and does not establish

This is primarily a **generative calibration / posterior-predictive-style check**: after fitting the historical process, it asks whether simulated scenarios reproduce selected properties of that process. It does **not** establish out-of-sample forecasting skill, causal geopolitical understanding, or adequacy for genuinely unprecedented future regimes.

The horizon-matched family removes an estimator confound; it does not remove the deeper limitation that both sides are being compared against a single historical realization. The overlapping windows make the year-level comparison descriptive rather than inferential, and the 16 independent years in this sample are the binding constraint on everything said about the stressed region.

A production validation programme would add rolling-origin and regime holdouts, parameter-stability monitoring, explicit stress-period tests, sensitivity to the futures-series construction, and model-risk governance. `BZ=F` is a convenient front-month proxy, not a professionally engineered constant-maturity Brent series; roll and contract-construction effects are therefore a known data limitation.
