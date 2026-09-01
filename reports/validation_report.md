# Brent synthetic-scenario validation report

## Scope

Daily Brent crude `BZ=F` close prices are fetched in code from Yahoo Finance. The analysis uses percentage log returns and a fixed historical cut-off for reproducibility.

## Statistical diagnostics

The sample contains **4,158** daily observations. Mean daily log return is 0.0029% with volatility 2.344%. The distribution is negatively skewed (-0.9584) and has excess kurtosis 14.691, which is inconsistent with a thin-tailed Gaussian description. A fitted Student-t reference has approximately **2.97 degrees of freedom**, providing a compact heavy-tailed benchmark for the QQ diagnostic.

Linear return dependence is comparatively limited: the maximum absolute return ACF over the inspected non-zero lags is 0.0503. In contrast, the mean absolute squared-return ACF is 0.1271, which is evidence of conditional heteroskedasticity / volatility clustering. That diagnostic directly motivates a volatility-aware generator rather than iid Monte Carlo.

I therefore use a **GARCH(1,1) with Student-t innovations** as a parsimonious baseline: GARCH targets volatility persistence while Student-t innovations target heavy marginal tails. The model does not claim to create genuinely new geopolitical regimes, asymmetric shock responses, or multivariate market dependence. Those are intentionally left as extensions if validation exposes material tail or regime failures.

![ACF diagnostics](figures/diagnostics_acf.png)

![Student-t QQ](figures/tail_qq_student_t.png)

## Fitted generative model

**GARCH(1,1) + standardized Student-t innovations**

| Parameter | Estimate |
|---|---:|
| mu | 0.0643 |
| omega | 0.0596 |
| alpha | 0.0885 |
| beta | 0.9045 |
| alpha + beta | 0.9930 |
| Student-t nu | 5.084 |

The simulation interface is explicitly fit-then-simulate and uses a fixed random seed. Student-t draws are standardized to unit variance before entering the GARCH recursion.

## Validation gates

These are pragmatic model acceptance gates rather than formal significance levels. Central-distribution and volatility targets have tighter tolerances; 99% tail measures and drawdowns are looser because their effective sample sizes are smaller.

| Metric | Real | Synthetic | Error | Threshold | Status |
|---|---:|---:|---:|---:|:---:|
| mean return (pp) | 0.0029 | 0.0650 | 0.0621 | 0.1000 | PASS |
| volatility | 2.344 | 2.890 | 23.3% | 10% | FAIL |
| skewness | -0.9584 | 0.6460 | 1.604 | 0.5000 | FAIL |
| excess kurtosis | 14.691 | 42.805 | 28.113 | 2.000 | FAIL |
| q01 | -6.707 | -7.799 | 16.3% | 20% | PASS |
| q05 | -3.639 | -3.888 | 6.8% | 15% | PASS |
| q95 | 3.244 | 4.014 | 23.7% | 15% | FAIL |
| q99 | 5.955 | 8.038 | 35.0% | 20% | FAIL |
| VaR 95% | 3.639 | 3.888 | 6.8% | 15% | PASS |
| ES 95% | 5.756 | 6.573 | 14.2% | 20% | PASS |
| VaR 99% | 6.707 | 7.799 | 16.3% | 20% | PASS |
| ES 99% | 9.939 | 12.018 | 20.9% | 25% | PASS |
| squared-return ACF MAE (lags 1-20) | 0.0000 | 0.0618 | 0.0618 | 0.0500 | FAIL |
| drawdown median | 0.2459 | 0.2868 | 16.6% | 25% | PASS |
| drawdown p95 | 0.6951 | 0.6095 | 12.3% | 30% | PASS |

![Marginal comparison](figures/marginal_comparison.png)

![Squared ACF comparison](figures/squared_acf_real_vs_synthetic.png)

![Drawdown comparison](figures/drawdown_distribution.png)

## Failure mode and next step

- **volatility** fails its declared gate. This is retained as evidence rather than tuned away.
- **skewness** fails its declared gate. This is retained as evidence rather than tuned away.
- **excess kurtosis** fails its declared gate. This is retained as evidence rather than tuned away.
- **q95** fails its declared gate. This is retained as evidence rather than tuned away.
- **q99** fails its declared gate. This is retained as evidence rather than tuned away.
- **squared-return ACF MAE (lags 1-20)** fails its declared gate. This is retained as evidence rather than tuned away.

The first extension I would test is conditional-tail modelling of standardized GARCH residuals using Peaks Over Threshold / Generalized Pareto tails (GARCH-EVT), especially if the failures concentrate in 99% ES, extreme quantiles, or drawdowns. If failures instead reflect state-dependent persistence, a regime-switching or asymmetric volatility model would be more appropriate.

## What this validation does and does not establish

This is primarily a **generative calibration / posterior-predictive-style check**: after fitting the historical process, it asks whether simulated scenarios reproduce selected properties of that process. It does **not** establish out-of-sample forecasting skill or prove adequacy for future regimes.

A stronger production validation would add rolling-origin or regime holdouts, parameter stability checks, stress-period analysis, and explicit model-risk governance.
