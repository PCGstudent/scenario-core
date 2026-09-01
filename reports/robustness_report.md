# Multi-seed robustness analysis

Both models are fitted once to the same historical returns and simulated over seeds 40-49. Each seed uses the same horizon, number of paths, validation metrics, fitted-state initialization principle, and the single declared threshold table. Every seed draws from independent `SeedSequence` child streams, so replications do not share a generator stream. The purpose is not hyperparameter tuning; it is to separate structural behaviour from a single Monte Carlo realization, and to show which of the two estimator families is stable enough to select a model with.

## Analytical persistence and fourth-moment diagnostics

- Baseline GARCH(1,1)-t: `E[(alpha z^2 + beta)^2] = 1.0451`; persistence 0.9930; implied unconditional volatility 2.9260%/day.
- Challenger GJR-skew-t: effective persistence 0.9935, computed as `alpha + beta + gamma * E[z^2 I(z<0)]` under the fitted skew-t law; `E[A(z)^2] = 1.0457`; implied unconditional volatility 2.9361%/day against a sample volatility of 2.3437%/day.

Neither model satisfies the usual finite unconditional fourth-moment condition. This is not a defect of one candidate over the other: it is a property of Brent at this sample length, and it is the structural reason why any pooled kurtosis comparison is unstable for both.

## Which estimator family can a decision be based on?

| Model | Pooled gates (median, range) | Matched gates (median, range) | Pooled capped error | Matched capped error | Explosive years (median / worst) |
|---|---:|---:|---:|---:|---:|
| baseline | 10.5/15 (10-12) | 14.0/15 (14-15) | 15.75 | 7.69 | 4.2% / 6.3% |
| challenger | 11.5/15 (10-13) | 15.0/15 (15-15) | 13.53 | 4.15 | 4.7% / 6.0% |

The aggregate error is a sum of per-gate error/threshold ratios capped at 5. Without the cap the sum is not a calibration summary at all: the unbounded kurtosis ratio alone accounts for most of it, so two models could be ranked entirely by a statistic that has no population limit. The cap is declared here rather than chosen after inspecting the ranking.

## Seed-to-seed stability of each statistic

Median across seeds, with the full range in brackets and the range as a percentage of the median. This is the core evidence for which family is a usable instrument.

| Metric | Pooled: median [range] | Pooled spread | Matched: median [range] | Matched spread |
|---|---:|---:|---:|---:|
| volatility | 2.6585 [2.5719, 2.8270] | 10% | 1.9530 [1.9168, 1.9920] | 4% |
| skewness | -0.7166 [-4.8760, -0.3241] | 635% | -0.2794 [-0.3128, -0.2647] | 17% |
| excess kurtosis | 53.6944 [33.9440, 389.4929] | 662% | 2.2713 [2.1956, 2.4119] | 10% |
| q01 | -7.5706 [-7.9058, -7.4237] | 6% | -5.3652 [-5.4805, -5.2425] | 4% |
| q99 | 6.7401 [6.5507, 7.0835] | 8% | 4.7954 [4.6437, 4.8892] | 5% |
| VaR 99% | 7.5706 [7.4237, 7.9058] | 6% | 5.3652 [5.2425, 5.4805] | 4% |
| ES 99% | 12.1192 [11.2766, 12.6963] | 12% | 6.6460 [6.4902, 6.7709] | 4% |
| squared-return ACF MAE | 0.0656 [0.0641, 0.0676] | 5% | 0.0187 [0.0175, 0.0203] | 15% |
| drawdown p95 | 0.6408 [0.6166, 0.6543] | 6% | 0.6408 [0.6166, 0.6543] | 6% |

The pooled estimates of the moment-based statistics swing by large multiples across seeds while the horizon-matched estimates of the same quantities barely move. That is the signature of a statistic dominated by a handful of explosive paths rather than by the generator's typical behaviour, and it is why model selection below uses the horizon-matched family and the structural diagnostics, not the pooled scorecard.

## Model-selection conclusion

I select the **GJR-GARCH skew-t challenger**. The decisive evidence is the horizon-matched skewness: Brent's typical year has a clearly negative return asymmetry (median -0.279 for the challenger against 0.003 for the symmetric baseline, on a historical target that no symmetric innovation law can reach by construction), and the challenger also improves right-tail quantile and extreme-drawdown calibration. Those gains persist across every seed while the added structure remains small and interpretable.

The choice is not uniform dominance and is not presented as such. The baseline is closer on some left-tail measures, and both models share the same structural defect: a near-integrated variance recursion with no finite fourth moment, which produces the explosive-year fractions in the table above. Selecting between them does not fix that; it is a model-risk finding that both inherit.

The selection is therefore conditional. In a production stress engine I would test GARCH-EVT for conditional tails and a regime-aware volatility specification to bound the explosive paths, with rolling-origin and regime holdouts, before treating either model as production-ready.
