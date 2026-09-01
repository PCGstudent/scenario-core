# 4-Xtra Senior DS/MLE Take-Home

A compact, reproducible R&D workflow for generating and validating synthetic Brent crude return scenarios.

The project follows the assessment cycle explicitly:

**diagnose → baseline → fit → simulate → validate → refine once → robustness-check → report limitations**

The submitted generator is **one model**: GJR-GARCH(1,1,1) with Hansen skewed-t innovations. A simpler GARCH(1,1)-Student-t remains in the repository as the development baseline that motivated the single refinement, and as the comparison the selection argument rests on. It is evidence about how the choice was made, not a second submission.

## Canonical clean-clone command

macOS/Linux:

```bash
bash run.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

The wrappers create `.venv`, install the exact pinned dependencies from `pyproject.toml`, run the test suite, generate the final submitted-model report/figures, regenerate the baseline-vs-challenger comparison, and run the 10-seed robustness analysis.

Individual analysis entry points are also available:

```bash
python -m xtra_takehome
python -m xtra_takehome.compare_models
python -m xtra_takehome.robustness
```

## Data and return definition

The code fetches Yahoo Finance Brent ticker `BZ=F` with `yfinance`:

- start: `2010-01-01`
- end: `2026-09-01` (exclusive)
- return: `100 * log(P_t / P_{t-1})`

The end date is fixed rather than `today()` so a clean clone has a reproducible analysis window. Raw market data is not committed. The first download is written to a gitignored `.cache/` so the three entry points in one run share a single fetch rather than hitting the API three times.

`BZ=F` is a convenient continuous/front-month proxy, not a professionally engineered constant-maturity Brent series. Roll and contract-construction effects may therefore contaminate some observed returns; this is treated as a data limitation rather than silently ignored.

## Why this model

Diagnostics show four features that drive the modelling decision:

1. weak linear dependence in daily returns;
2. materially stronger autocorrelation in squared returns, indicating volatility clustering;
3. negative skew, inconsistent with a symmetric innovation law;
4. a Hill tail index near 3 in **both** tails, against a fitted conditional innovation with about 5.3 degrees of freedom.

Point 4 is the one that decides the family. An unconditional tail markedly heavier than the conditional innovation is the signature of volatility clustering: a GARCH-type recursion driven by moderately heavy innovations produces an unconditional law heavier-tailed than those innovations. The fit then makes a checkable prediction. Solving `E[A(z)^k] = 1` for the fitted variance multiplier gives a **model-implied return tail index of 2.70**, against a **Hill estimate of 2.94** taken directly from the returns. Those agree to within about 8%, and the agreement is not circular: the tail index never entered the likelihood, which sees only the conditional density.

Point 4 also shows the asymmetry is not a tail-index asymmetry — the two tails decay at similar rates, so the skew lives in the body and in the volatility response, which is exactly what GJR plus a skewed innovation targets.

I first fitted **GARCH(1,1)-Student-t** because it is the smallest model that directly targets volatility clustering plus heavy tails. Validation exposed a material asymmetry miss. I therefore made one targeted refinement rather than jumping to a neural generator: **GJR-GARCH + skewed-t**.

The challenger was not selected from one favourable seed. `reports/robustness_report.md` compares both models over seeds 40–49 using identical data, horizons, path counts, initialization principles, metrics and thresholds.

## Submitted model

```text
r_t       = mu + eps_t
eps_t     = sigma_t z_t
sigma_t^2 = omega + alpha eps_(t-1)^2
            + gamma I(eps_(t-1) < 0) eps_(t-1)^2
            + beta sigma_(t-1)^2
z_t       ~ standardized Hansen skewed-t(eta, lambda)
```

It is intended to reproduce:

- heavy-tailed daily return behaviour;
- negative return asymmetry;
- time-varying conditional volatility and volatility clustering;
- plausible one-year tail-loss and drawdown distributions for typical and moderately adverse years.

It is **not** intended to reproduce:

- causal geopolitical mechanisms or genuinely unprecedented shocks;
- structural/regime changes with a single stationary recursion;
- multivariate dependence with rates, FX, equities or other commodities;
- a professionally constructed constant-maturity Brent futures curve;
- the far upper tail of year severity, where the fitted recursion is not fourth-moment stationary (see the model-risk finding below).

## How validation works

Every metric is checked under two estimators, plus a third family of checks for the region where neither estimator can help.

**Family 1, pooled marginal.** All synthetic observations pooled against the pooled historical sample: 252,000 against 4,158. The right instrument for the unconditional marginal law, and the wrong one for any statistic that depends on sample size.

**Family 2, horizon-matched.** Every statistic estimated inside 252-day blocks on *both* sides — 3,907 overlapping historical windows against 1,000 independent synthetic paths — then compared at the median. This family exists because the sample ACF of squared returns is biased toward zero in short blocks, and because the fitted recursion has no finite unconditional fourth moment, so sample kurtosis has no population value to converge to. Comparing either across unequal sample sizes measures the estimator, not the model.

**Two tolerances cannot be constants**, and treating them as constants was a real defect that an audit of an earlier version caught. Carrying the same *absolute* squared-return ACF tolerance into the block estimator quietly relaxed the gate to the point where it could not fail: the historical target shrinks from 0.127 to 0.046 under that estimator, so an iid bootstrap of the real returns — the exact historical marginal with no volatility clustering at all — scored 0.0498 against a 0.05 threshold and passed. Those tolerances are now declared as a fraction of the historical scale *under the estimator in use*, fixed at the fraction the original absolute number implied. The strictness is unchanged; only the units travel. A regression test pins this by asserting the gate rejects a zero-clustering generator. The mean-return tolerance is likewise expressed in standard errors of the historical mean, since an absolute tolerance on a daily mean has no scale.

Drawdowns are horizon-matched by construction, so they are computed once and reported once rather than duplicated into both scorecards.

**Family 3, the stressed region.** Two tempting comparisons are avoided here, and the report says why rather than quietly reporting them. Gating on an upper historical percentile is meaningless: with overlapping windows the top few per cent of blocks are one episode repeated — the 222 blocks above the 95% ES-99% quantile all begin between 2019-04-23 and 2020-03-09 — so that quantile is not identified. Comparing `max(1,000 simulated years)` with `max(16 observed years)` is the same error in disguise, since the maximum of a heavy-tailed sample grows with the sample. Instead the model is projected onto a record of the same length as the historical one, asking how likely 16 years are to contain nothing worse than what was observed. Nothing in this family is a formal gate: 16 non-overlapping blocks — which are not 16 independent observations — do not support one.

A separate **matched-length reference** settles the pooled moments directly: whole continuous records of the historical length are simulated, and the observed value is located in the model's own distribution. Concatenating independent years would not do — with persistence at 0.9935 roughly a fifth of the variance memory survives a year, so stitching would discard any volatility episode crossing a boundary.

Covered metrics: mean, volatility, skewness, excess kurtosis; the 1%, 5%, 95% and 99% return quantiles; VaR and ES at 95% and 99%; squared-return ACF over lags 1–20; and maximum drawdown over 252-day horizons. VaR and ES use **loss `L = -return`** and are reported as positive loss magnitudes. Squared-return ACF is always estimated within a path and then averaged; independent paths are never concatenated.

Acceptance thresholds are pragmatic engineering gates, not hypothesis-test significance levels, and FAILs are retained rather than tuned away.

## What the model gets right, and what it gets wrong

The pooled family fails five gates and the horizon-matched family fails one. Neither count is the finding; what the difference between them means is.

**The pooled moment failures are consistent with finite-record variability.** The evidence is simulation of whole records of the same length as the historical one — continuous 4,158-day paths, initialized once, not stitched together from independent years. The historical volatility, skewness and excess kurtosis land at the 45th, 17th and 72nd percentile of the model's own distribution over such records, all inside its 5–95% band. A single 16-year record does not pin these quantities down: the model's own records disagree with each other by more than the model disagrees with history. This does not prove the marginals are correctly calibrated — it is an in-sample generative check — but it does mean these three failures are not, by themselves, evidence of miscalibration.

**The genuine failure is the shape of volatility memory.** The horizon-matched squared-return ACF misses its gate at 0.0191 against a tolerance of 0.0180. That is not sampling noise: two independent simulations of this same model differ from each other by only 0.0032 on the identical statistic, so the discrepancy with history is roughly six times the irreducible simulation noise. The historical block ACF decays slowly and irregularly; the model's decays geometrically. A single stationary GJR recursion reproduces the average level of volatility persistence without reproducing its profile.

**The material limitation is extrapolation, not calibration.** At the edge of the record the observed extreme is plausible under the fitted model: the worst observed year sits in the middle of the model's predicted distribution for a 16-year record on all four severity measures, with P(record max ≤ observed) between 46% and 69%. Beyond that edge there is nothing to calibrate against, and because `E[A(z)^2] = 1.0457 ≥ 1` the fitted process has no finite unconditional fourth moment, so the extrapolation is unusually heavy. This generator should not produce a far-tail capital number without an explicitly governed cap, or without a specification whose stationary law has the moments the use case assumes. That is a statement about where the model may be trusted rather than a defect in its fit — the same property that makes the extrapolation heavy is what lets it reproduce the unconditional tail index it was never fitted to.

`reports/robustness_report.md` shows which statistics can carry a decision. Across ten seeds, pooled excess kurtosis ranges over [33.9, 389.5] and pooled skewness over [-4.88, -0.32], while the horizon-matched estimates of the same two quantities stay within [2.20, 2.41] and [-0.31, -0.26]. Most other metrics are reasonably stable under both estimators. No number from the pooled column should be quoted as characteristic of the generator.

The next experiment would be **GARCH-EVT** if conditional-tail calibration is the priority, or a **regime-aware volatility model** to test whether state-dependent persistence reproduces the ACF profile a single recursion misses. I intentionally stop before those extensions.

## Outputs

```text
reports/
├── validation_report.md        # final submitted-model report
├── fit_summary.txt             # arch optimizer/model summary
├── run_manifest.json           # data/model/simulation/validation provenance
├── model_comparison.md         # development baseline vs challenger, both families
├── robustness_report.md        # 10-seed stability and model-risk analysis
└── figures/
    ├── diagnostics_acf.png
    ├── tail_qq_student_t.png
    ├── tail_index_and_mean_excess.png
    ├── marginal_comparison.png
    ├── squared_acf_real_vs_synthetic.png
    ├── year_severity.png
    └── drawdown_distribution.png
```

## Repository structure

```text
.
├── AGENTS.md
├── AIUSAGE.md
├── AWS_DESIGN.md
├── README.md
├── pyproject.toml
├── run.ps1
├── run.sh
├── src/xtra_takehome/
│   ├── __main__.py
│   ├── challenger.py          # submitted GJR-skew-t generator
│   ├── compare_models.py
│   ├── config.py
│   ├── data.py
│   ├── diagnostics.py
│   ├── metrics.py
│   ├── model.py               # development GARCH-t baseline
│   ├── plots.py
│   ├── report.py
│   ├── robustness.py
│   ├── validation.py
│   └── windows.py             # horizon-matched block statistics
└── tests/
```

## Reproducibility and AI use

Every stochastic operation is seed-controlled. Simulation draws its state-sampling and innovation streams from independent `SeedSequence(seed).spawn(2)` children, so replications in the robustness study never share a generator stream. The fitted model uses an explicit fit-then-simulate interface, and calibration simulations start from sampled historical fitted residual/variance states so paths cover empirically observed calm and stressed initial conditions. `run_manifest.json` records the data window, path count, horizon, seed, RNG scheme, fitted parameters, persistence and higher-moment diagnostics, and the results of all three validation families.

One caveat, verified rather than assumed. The committed reports were reproduced from a clean clone with the single documented command: the fetched price series is bit-identical, the suite passes, and every gate verdict matches. Re-running in the *same* environment reproduces the reports byte for byte. Across a differently built virtual environment the fitted parameters drift in the fifth significant figure, because the `arch` maximum-likelihood optimizer converges to a marginally different point depending on host BLAS threading; the resulting drift in reported statistics is below 0.1% and changed no PASS/FAIL outcome. Bit-level determinism across environments would require pinning BLAS thread counts, which I judged out of scope; the claim is reproducible conclusions, and reproducible digits within an environment.

AI-assisted development is documented in `AIUSAGE.md`, including what was delegated, what remained human review responsibility, and the concrete mistakes that review caught.

`AWS_DESIGN.md` describes an on-demand production path using API Gateway, Lambda, Step Functions, ECS Fargate, ECR, S3, DynamoDB and CloudWatch, including identity/secrets, conditioning state, cost and a 100× usage design.
