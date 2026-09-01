# Baseline vs challenger

This development comparison uses the same data, horizon, number of paths, seed, validation metrics, and acceptance gates for both models.

## Models

- **Baseline:** GARCH(1,1) with Student-t innovations.
- **Challenger:** GJR-GARCH(1,1,1) with Hansen skewed-t innovations and empirical fitted-state initialization.

Baseline gates passed: **9/15**; aggregate normalized gate error: **29.943**.
Challenger gates passed: **12/15**; aggregate normalized gate error: **38.289**.
Provisional winner under the declared rule: **challenger**.

The pass count is the primary criterion; aggregate error relative to each declared threshold is only a tie-breaker. This is a development aid, not an excuse to tune thresholds after observing results.

## Fitted challenger parameters

- mu: -0.000262
- omega: 0.056438
- alpha: 0.065334
- gamma: 0.034442
- beta: 0.909230
- approximate persistence alpha + gamma/2 + beta: 0.991786
- skew-t eta: 5.293442
- skew-t lambda: -0.121462

## Gate-by-gate comparison

| Model | Metric | Real | Synthetic | Error | Threshold | Status |
|---|---|---:|---:|---:|---:|:---:|
| baseline | mean return (pp) | 0.0029 | 0.0650 | 0.0621 | 0.1000 | PASS |
| baseline | volatility | 2.3437 | 2.8900 | 23.3% | 10% | FAIL |
| baseline | skewness | -0.9584 | 0.6460 | 1.6045 | 0.5000 | FAIL |
| baseline | excess kurtosis | 14.6915 | 42.8049 | 28.1135 | 2.0000 | FAIL |
| baseline | q01 | -6.7071 | -7.7990 | 16.3% | 20% | PASS |
| baseline | q05 | -3.6392 | -3.8880 | 6.8% | 15% | PASS |
| baseline | q95 | 3.2444 | 4.0135 | 23.7% | 15% | FAIL |
| baseline | q99 | 5.9550 | 8.0381 | 35.0% | 20% | FAIL |
| baseline | VaR 95% | 3.6392 | 3.8880 | 6.8% | 15% | PASS |
| baseline | ES 95% | 5.7562 | 6.5727 | 14.2% | 20% | PASS |
| baseline | VaR 99% | 6.7071 | 7.7990 | 16.3% | 20% | PASS |
| baseline | ES 99% | 9.9387 | 12.0175 | 20.9% | 25% | PASS |
| baseline | squared-return ACF MAE (lags 1-20) | 0.0000 | 0.0618 | 0.0618 | 0.0500 | FAIL |
| baseline | drawdown median | 0.2459 | 0.2868 | 16.6% | 25% | PASS |
| baseline | drawdown p95 | 0.6951 | 0.6095 | 12.3% | 30% | PASS |
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

## Decision note

The challenger is justified only if the observed negative skew / volatility asymmetry and validation results improve enough to warrant the extra parameterization. If not, the simpler baseline should remain the submission model.
