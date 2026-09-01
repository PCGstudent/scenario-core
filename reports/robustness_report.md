# Multi-seed robustness analysis

Both models are fitted once to the same historical returns and simulated over seeds 40-49. Each seed uses the same horizon, number of paths, validation metrics, and fixed acceptance gates. The purpose is not hyperparameter tuning; it is to distinguish structural behaviour from a single Monte Carlo realization.

## Fourth-moment diagnostic for the baseline

For the fitted GARCH(1,1)-Student-t baseline, `E[(alpha z^2 + beta)^2] = 1.0451`.

Because this is >= 1, the fitted process does not satisfy the usual finite unconditional fourth-moment condition; sample kurtosis can therefore be intrinsically unstable across simulations.

## Aggregate stability

| Model | Median gates | Range | Median normalized error | Worst normalized error | Median severe fails | Worst severe fails |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 10.0/15 | 10-12 | 22.94 | 91.94 | 1.0 | 3 |
| challenger | 12.0/15 | 11-13 | 26.98 | 102.94 | 1.0 | 2 |

## Metric stability (median [10th, 90th percentile])

| Metric | Baseline | Challenger |
|---|---:|---:|
| volatility | 2.6062 [2.5330, 2.7216] | 2.6188 [2.4600, 2.7098] |
| skewness | -0.0693 [-0.3377, 0.4275] | -0.8198 [-1.1257, -0.4556] |
| excess kurtosis | 40.7120 [28.6829, 83.2880] | 53.3015 [29.4303, 125.4141] |
| q01 | -7.1074 [-7.2291, -7.0131] | -7.4017 [-7.7636, -7.1622] |
| q99 | 7.2584 [7.0769, 7.3400] | 6.5461 [6.3828, 6.8647] |
| VaR 99% | 7.1074 [7.0131, 7.2291] | 7.4017 [7.1622, 7.7636] |
| ES 99% | 11.1126 [10.5406, 11.8645] | 11.6644 [10.7553, 12.4728] |
| squared-return ACF MAE (lags 1-20) | 0.0672 [0.0661, 0.0678] | 0.0668 [0.0653, 0.0681] |
| drawdown p95 | 0.5813 [0.5470, 0.6051] | 0.6310 [0.6067, 0.6534] |

## Decision principle

I would not select a model from a single seed or from pass count alone. I prefer the model whose improvements are stable across seeds, whose severe failures are fewer, and whose known structural limitations are easiest to explain and govern. The far-tail metrics and kurtosis receive special scrutiny because this application is explicitly about stress scenarios.
