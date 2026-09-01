# Baseline vs challenger

This is a development comparison, not the final model-selection rule. Both models use the same data, horizon, number of paths, seed, fitted-state initialization principle, validation metrics, and the single declared threshold table in `validation.THRESHOLDS`.

## Models

- **Baseline:** GARCH(1,1) with Student-t innovations.
- **Challenger / submitted model:** GJR-GARCH(1,1,1) with Hansen skewed-t innovations.

Every metric is scored twice: once on the pooled synthetic sample, and once with each statistic estimated inside 252-day blocks on both sides. The two families share their thresholds and differ only in the estimator.

| Model | Pooled gates | Horizon-matched gates | Pooled capped error | Matched capped error | Years more volatile than any observed |
|---|---:|---:|---:|---:|---:|
| baseline | 11/15 | 14/15 | 13.94 | 7.92 | 4.7% |
| challenger | 10/15 | 15/15 | 18.48 | 4.54 | 4.4% |

I deliberately do **not** declare a winner from this one realization. An early AI-assisted comparison used pass count as the primary winner rule; reviewing the tail errors showed that this was too simplistic, and the multi-seed analysis later showed that the pooled scorecard is not even stable enough to rank two models with. The selection decision therefore uses `robustness_report.md`, the horizon-matched family, failure severity, and structural diagnostics.

## Fitted parameters

| Quantity | Baseline | Challenger |
|---|---:|---:|
| mu | 0.064277 | -0.000255 |
| omega | 0.059618 | 0.056427 |
| alpha | 0.088494 | 0.065331 |
| gamma | - | 0.034429 |
| beta | 0.904542 | 0.909242 |
| effective persistence | 0.993036 | 0.993455 |
| implied unconditional volatility (%/day) | 2.9260 | 2.9361 |
| innovation shape | nu = 5.0840 | eta = 5.2933, lambda = -0.1215 |

For the skewed innovation law, effective persistence uses `alpha + beta + gamma * E[z^2 I(z<0)]`; the symmetric `gamma/2` approximation is not used. Both models imply an unconditional volatility above the sample volatility of 2.3437%/day, which is the source of the explosive paths reported in the last column of the table above.

## Gate-by-gate comparison, pooled

| Model | Metric | Real | Synthetic | Error | Threshold | Status |
|---|---|---:|---:|---:|---:|:---:|
| baseline | mean return (pp) | 0.0029 | 0.0668 | 0.0638 | 0.1000 | PASS |
| baseline | volatility | 2.3437 | 2.5894 | 10.5% | 10% | FAIL |
| baseline | skewness | -0.9584 | -0.1463 | 0.8121 | 0.5000 | FAIL |
| baseline | excess kurtosis | 14.6915 | 50.2581 | 35.5666 | 2.0000 | FAIL |
| baseline | q01 | -6.7071 | -7.0799 | 5.6% | 20% | PASS |
| baseline | q05 | -3.6392 | -3.4542 | 5.1% | 15% | PASS |
| baseline | q95 | 3.2444 | 3.6220 | 11.6% | 15% | PASS |
| baseline | q99 | 5.9550 | 7.1426 | 19.9% | 20% | PASS |
| baseline | VaR 95% | 3.6392 | 3.4542 | 5.1% | 15% | PASS |
| baseline | ES 95% | 5.7562 | 5.9118 | 2.7% | 20% | PASS |
| baseline | VaR 99% | 6.7071 | 7.0799 | 5.6% | 20% | PASS |
| baseline | ES 99% | 9.9387 | 10.8994 | 9.7% | 25% | PASS |
| baseline | squared-return ACF MAE | 0.0000 | 0.0693 | 0.0693 | 0.0500 | FAIL |
| baseline | drawdown median | 0.2459 | 0.2506 | 1.9% | 25% | PASS |
| baseline | drawdown p95 | 0.6951 | 0.5626 | 19.1% | 30% | PASS |
| challenger | mean return (pp) | 0.0029 | -0.0043 | 0.0072 | 0.1000 | PASS |
| challenger | volatility | 2.3437 | 2.8270 | 20.6% | 10% | FAIL |
| challenger | skewness | -0.9584 | -4.8760 | 3.9175 | 0.5000 | FAIL |
| challenger | excess kurtosis | 14.6915 | 389.4929 | 374.8014 | 2.0000 | FAIL |
| challenger | q01 | -6.7071 | -7.5341 | 12.3% | 20% | PASS |
| challenger | q05 | -3.6392 | -3.6699 | 0.8% | 15% | PASS |
| challenger | q95 | 3.2444 | 3.3814 | 4.2% | 15% | PASS |
| challenger | q99 | 5.9550 | 6.7498 | 13.3% | 20% | PASS |
| challenger | VaR 95% | 3.6392 | 3.6699 | 0.8% | 15% | PASS |
| challenger | ES 95% | 5.7562 | 6.5039 | 13.0% | 20% | PASS |
| challenger | VaR 99% | 6.7071 | 7.5341 | 12.3% | 20% | PASS |
| challenger | ES 99% | 9.9387 | 12.6963 | 27.7% | 25% | FAIL |
| challenger | squared-return ACF MAE | 0.0000 | 0.0649 | 0.0649 | 0.0500 | FAIL |
| challenger | drawdown median | 0.2459 | 0.2848 | 15.8% | 25% | PASS |
| challenger | drawdown p95 | 0.6951 | 0.6189 | 11.0% | 30% | PASS |

## Gate-by-gate comparison, horizon-matched

| Model | Metric | Real median | Synthetic median | Error | Threshold | Status |
|---|---|---:|---:|---:|---:|:---:|
| baseline | mean return (pp) | -0.0055 | 0.0677 | 0.0732 | 0.1000 | PASS |
| baseline | volatility | 1.9658 | 1.9893 | 1.2% | 10% | PASS |
| baseline | skewness | -0.4525 | 0.0047 | 0.4572 | 0.5000 | PASS |
| baseline | excess kurtosis | 2.1315 | 2.3577 | 0.2262 | 2.0000 | PASS |
| baseline | q01 | -5.6359 | -5.0174 | 11.0% | 20% | PASS |
| baseline | q05 | -3.3411 | -2.9887 | 10.5% | 15% | PASS |
| baseline | q95 | 2.8479 | 3.1781 | 11.6% | 15% | PASS |
| baseline | q99 | 4.2737 | 5.1763 | 21.1% | 20% | FAIL |
| baseline | VaR 95% | 3.3411 | 2.9887 | 10.5% | 15% | PASS |
| baseline | ES 95% | 4.6394 | 4.3571 | 6.1% | 20% | PASS |
| baseline | VaR 99% | 5.6359 | 5.0174 | 11.0% | 20% | PASS |
| baseline | ES 99% | 6.8891 | 6.2736 | 8.9% | 25% | PASS |
| baseline | squared-return ACF MAE | 0.0000 | 0.0167 | 0.0167 | 0.0500 | PASS |
| baseline | drawdown median | 0.2459 | 0.2506 | 1.9% | 25% | PASS |
| baseline | drawdown p95 | 0.6951 | 0.5626 | 19.1% | 30% | PASS |
| challenger | mean return (pp) | -0.0055 | 0.0124 | 0.0179 | 0.1000 | PASS |
| challenger | volatility | 1.9658 | 1.9292 | 1.9% | 10% | PASS |
| challenger | skewness | -0.4525 | -0.2696 | 0.1829 | 0.5000 | PASS |
| challenger | excess kurtosis | 2.1315 | 2.2907 | 0.1591 | 2.0000 | PASS |
| challenger | q01 | -5.6359 | -5.2979 | 6.0% | 20% | PASS |
| challenger | q05 | -3.3411 | -3.1475 | 5.8% | 15% | PASS |
| challenger | q95 | 2.8479 | 2.8922 | 1.6% | 15% | PASS |
| challenger | q99 | 4.2737 | 4.7423 | 11.0% | 20% | PASS |
| challenger | VaR 95% | 3.3411 | 3.1475 | 5.8% | 15% | PASS |
| challenger | ES 95% | 4.6394 | 4.5387 | 2.2% | 20% | PASS |
| challenger | VaR 99% | 5.6359 | 5.2979 | 6.0% | 20% | PASS |
| challenger | ES 99% | 6.8891 | 6.5156 | 5.4% | 25% | PASS |
| challenger | squared-return ACF MAE | 0.0000 | 0.0191 | 0.0191 | 0.0500 | PASS |
| challenger | drawdown median | 0.2459 | 0.2848 | 15.8% | 25% | PASS |
| challenger | drawdown p95 | 0.6951 | 0.6189 | 11.0% | 30% | PASS |

## Selection rationale

The asymmetric challenger is retained because it captures the observed negative skew materially better and improves right-tail quantiles and extreme drawdown calibration, while the added structure remains small and interpretable. It is not uniformly better: the baseline is somewhat closer on some left-tail measures. Under the pooled family the challenger's kurtosis and skewness misses look catastrophic, but the multi-seed analysis shows those pooled statistics swinging by several hundred per cent across seeds for both models, so they are not a basis for ranking. Both models share the near-integrated variance recursion and the resulting explosive years; that is recorded as a model-risk finding inherited by whichever is selected, not averaged into a score.
