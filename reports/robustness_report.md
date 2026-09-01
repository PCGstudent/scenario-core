# Multi-seed robustness analysis

Both models are fitted once to the same historical returns and simulated over seeds 40-49. Each seed uses the same horizon, path count, metrics and tolerances, and draws from independent `SeedSequence` child streams so replications never share a generator stream. The purpose is not tuning; it is to separate structural behaviour from a single Monte Carlo realization, and to establish which estimator is stable enough to select a model with.

## Analytical persistence and fourth-moment diagnostics

- Baseline GARCH(1,1)-t: `E[(alpha z^2 + beta)^2] = 1.0451`; persistence 0.9930; implied unconditional volatility 2.9260%/day.
- Challenger GJR-skew-t: effective persistence 0.9935, computed as `alpha + beta + gamma * E[z^2 I(z<0)]` under the fitted skew-t law; `E[A(z)^2] = 1.0457`; implied unconditional volatility 2.9361%/day against a sample volatility of 2.3437%/day; implied return tail index 2.696.

Both fitted specifications land in a near-integrated region that does not satisfy the finite unconditional fourth-moment condition on this sample. That is a property of these two fits, not of Brent itself: a different specification, or the same one on a different window, need not land there. Its consequence is that pooled sample kurtosis has no population value to converge to under either model, which is why the two estimator families below disagree so violently on that one statistic.

## Which estimator can a decision be based on?

| Model | Pooled gates (median, range) | Horizon-matched gates (median, range) | Years above any observed (median / worst) |
|---|---:|---:|---:|
| baseline | 9.5/14 (8-11) | 11.0/13 (11-13) | 4.2% / 6.3% |
| challenger | 10.5/14 (9-12) | 12.0/13 (12-13) | 4.7% / 6.0% |

No scalar aggregate score is reported. An earlier version summed per-gate error/threshold ratios, which the unbounded kurtosis ratio dominated so completely that the score was a restatement of one statistic; capping the ratio only replaced that problem with an arbitrary cap. Pass counts, the metric table below and the structural diagnostics above carry the information without inventing a number.

The last column is named for what it measures and is not by itself a defect. With a record of only sixteen non-overlapping years, a correctly calibrated heavy-tailed generator *should* place a few per cent of years beyond anything observed; the validation report checks that frequency explicitly and finds it plausible for both.

## Seed-to-seed stability of each statistic

Median across seeds, full range in brackets, and the range as a percentage of the median. This is the core evidence for which estimator is a usable instrument.

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

The contrast is specific rather than uniform, and worth stating precisely. Most metrics are reasonably stable under both estimators. Two are not: pooled excess kurtosis ranges over [33.9, 389.5] across ten seeds while the horizon-matched estimate of the same quantity stays within [2.20, 2.41], and pooled skewness ranges over [-4.88, -0.32]. Those are exactly the two statistics that have no finite population value under a process without a fourth moment. A single seed of the pooled family can therefore report a kurtosis miss an order of magnitude larger than another seed of the identical model, which is why no number from that column should be quoted as characteristic of the generator.

## Model-selection conclusion

I select the **GJR-GARCH skew-t challenger**, on error magnitude rather than on any single gate. The clearest evidence is asymmetry: Brent's typical year has a markedly negative return skew, and the challenger's horizon-matched median is -0.279 against 0.003 for the symmetric baseline, on a target no symmetric innovation law can reach by construction. Both models happen to clear the skewness tolerance, so this is a difference in fit quality rather than a gate outcome, and it would be inflation to call it decisive on the gate alone. The challenger also improves right-tail quantile calibration, where the baseline is the one that misses.

The choice is not uniform dominance. The baseline is closer on some left-tail measures, and both models share the same structural limitation: a near-integrated variance recursion with no finite fourth moment. Selecting between them does not address that, and it is recorded as a model-risk finding inherited by whichever is chosen.

In a production stress engine I would test GARCH-EVT for conditional tails and a regime-aware specification for the volatility-memory shape, with rolling-origin and regime holdouts, before treating either model as production-ready.
