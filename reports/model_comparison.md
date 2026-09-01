# Baseline vs challenger

This is a development comparison, not the final model-selection rule. Both models use the same data, horizon, number of paths, seed, fitted-state initialization principle, validation metrics, and acceptance gates.

## Models

- **Baseline:** GARCH(1,1) with Student-t innovations.
- **Challenger / submitted model:** GJR-GARCH(1,1,1) with Hansen skewed-t innovations.

Baseline: **10/15** gates; aggregate normalized gate error **24.715**; severe failures **2**.
Challenger: **12/15** gates; aggregate normalized gate error **38.289**; severe failures **1**.

I deliberately do **not** declare a winner from this one realization. An early AI-assisted comparison used pass count as the primary winner rule; review of the tail errors showed that this was too simplistic. The selection decision therefore uses the multi-seed robustness analysis in `robustness_report.md`, failure severity, and model interpretability in addition to this table.

## Fitted challenger parameters

- mu: -0.000262
- omega: 0.056438
- alpha: 0.065334
- gamma: 0.034442
- beta: 0.909230
- effective persistence: 0.993454
- skew-t eta: 5.293442
- skew-t lambda: -0.121462
- fourth-moment coefficient E[A(z)^2]: 1.045732

For the skewed innovation law, effective persistence uses `alpha + beta + gamma * E[z^2 I(z<0)]`; the symmetric `gamma/2` approximation is not used.

## Gate-by-gate comparison

| Model | Metric | Real | Synthetic | Error | Threshold | Status |
|---|---|---:|---:|---:|---:|:---:|
| baseline | mean return (pp) | 0.0029 | 0.0628 | 0.0599 | 0.1000 | PASS |
| baseline | volatility | 2.3437 | 2.6631 | 13.6% | 10% | FAIL |
| baseline | skewness | -0.9584 | 0.1899 | 1.1483 | 0.5000 | FAIL |
| baseline | excess kurtosis | 14.6915 | 43.3406 | 28.6491 | 2.0000 | FAIL |
| baseline | q01 | -6.7071 | -7.1144 | 6.1% | 20% | PASS |
| baseline | q05 | -3.6392 | -3.4862 | 4.2% | 15% | PASS |
| baseline | q95 | 3.2444 | 3.6198 | 11.6% | 15% | PASS |
| baseline | q99 | 5.9550 | 7.2858 | 22.3% | 20% | FAIL |
| baseline | VaR 95% | 3.6392 | 3.4862 | 4.2% | 15% | PASS |
| baseline | ES 95% | 5.7562 | 6.0232 | 4.6% | 20% | PASS |
| baseline | VaR 99% | 6.7071 | 7.1144 | 6.1% | 20% | PASS |
| baseline | ES 99% | 9.9387 | 11.2793 | 13.5% | 25% | PASS |
| baseline | squared-return ACF MAE (lags 1-20) | 0.0000 | 0.0672 | 0.0672 | 0.0500 | FAIL |
| baseline | drawdown median | 0.2459 | 0.2614 | 6.3% | 25% | PASS |
| baseline | drawdown p95 | 0.6951 | 0.5473 | 21.3% | 30% | PASS |
| challenger | mean return (pp) | 0.0029 | 0.0013 | 0.0016 | 0.1000 | PASS |
| challenger | volatility | 2.3437 | 2.5986 | 10.9% | 10% | FAIL |
| challenger | skewness | -0.9584 | -1.1941 | 0.2357 | 0.5000 | PASS |
| challenger | excess kurtosis | 14.6915 | 77.8881 | 63.1967 | 2.0000 | FAIL |
| challenger | q01 | -6.7071 | -7.3636 | 9.8% | 20% | PASS |
| challenger | q05 | -3.6392 | -3.6393 | 0.0% | 15% | PASS |
| challenger | q95 | 3.2444 | 3.3576 | 3.5% | 15% | PASS |
| challenger | q99 | 5.9550 | 6.5919 | 10.7% | 20% | PASS |
| challenger | VaR 95% | 3.6392 | 3.6393 | 0.0% | 15% | PASS |
| challenger | ES 95% | 5.7562 | 6.2222 | 8.1% | 20% | PASS |
| challenger | VaR 99% | 6.7071 | 7.3636 | 9.8% | 20% | PASS |
| challenger | ES 99% | 9.9387 | 11.4650 | 15.4% | 25% | PASS |
| challenger | squared-return ACF MAE (lags 1-20) | 0.0000 | 0.0671 | 0.0671 | 0.0500 | FAIL |
| challenger | drawdown median | 0.2459 | 0.2892 | 17.6% | 25% | PASS |
| challenger | drawdown p95 | 0.6951 | 0.6314 | 9.2% | 30% | PASS |

## Selection rationale

The asymmetric challenger was retained because its improvements in negative skew, tail quantiles and drawdown behaviour persisted across seeds, while the extra parameterization remains small and interpretable. The decision is not a claim that the challenger is fully adequate: higher-moment instability and squared-return ACF mismatch remain explicit model-risk findings.
