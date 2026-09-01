# 4-Xtra Senior DS/MLE Take-Home

A compact, reproducible R&D workflow for generating and validating synthetic Brent crude return scenarios.

The project follows the assessment cycle explicitly:

**diagnose → baseline → fit → simulate → validate → refine once → robustness-check → report limitations**

The submitted generator is **GJR-GARCH(1,1,1) with Hansen skewed-t innovations**. A simpler GARCH(1,1)-Student-t model remains in the repository as the development baseline that motivated the refinement.

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

Point 4 is the one that decides the family. An unconditional tail much heavier than the conditional innovation means roughly half the unconditional tail weight is manufactured by volatility clustering rather than by fat innovations — exactly what a GARCH-type recursion with moderately heavy innovations produces. It also shows the asymmetry is not a tail-index asymmetry: the two tails decay at similar rates, and the skew lives in the body and in the volatility response, which is what GJR plus a skewed innovation targets.

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

Every metric is checked twice against **one shared table of thresholds** declared in `validation.THRESHOLDS`. The two families differ only in how the statistic is estimated, never in the tolerance it must meet.

**Family 1, pooled marginal.** All synthetic observations pooled against the pooled historical sample. The right instrument for the unconditional marginal law.

**Family 2, horizon-matched.** Every statistic estimated inside 252-day blocks on *both* sides — 3,907 overlapping historical windows against 1,000 independent synthetic paths — then compared at the median.

The second family exists because several statistics are sample-size dependent, so comparing 252,000 pooled synthetic observations against ~4,158 historical ones measures the estimator rather than the model. The sample ACF of squared returns is biased toward zero in short blocks; and because the fitted recursion has no finite unconditional fourth moment, sample kurtosis has no limit to converge to and simply grows with the simulated sample size. This is the same like-for-like principle the original design already applied to drawdowns, extended to the rest of the suite.

**Family 3, worst-observed-year exceedance.** The stressed region is deliberately *not* gated on a historical percentile. With overlapping windows the top few per cent of blocks are one episode repeated — for this sample, the 195 most severe ES-99% windows all begin between April 2019 and March 2020 — so a historical "95th percentile" is not identified. There are only 16 independent trading years in the sample. What is identified is a frequency, so the model-implied annual probability of a year at least as severe as the worst observed one is checked against the exact 90% Poisson interval for having seen exactly one such year.

Covered metrics: mean, volatility, skewness, excess kurtosis; the 1%, 5%, 95% and 99% return quantiles; VaR and ES at 95% and 99%; squared-return ACF over lags 1–20; and maximum drawdown over 252-day horizons. VaR and ES use **loss `L = -return`** and are reported as positive loss magnitudes. Squared-return ACF is always estimated within a path and then averaged; independent paths are never concatenated.

Acceptance thresholds are pragmatic engineering gates, not hypothesis-test significance levels. They are fixed in code and FAILs are retained rather than tuned away.

## Model-risk finding

The generator is well calibrated for typical and moderately adverse years and passes every horizon-matched gate. Its material limitation is at the other end: **a near-integrated variance recursion that occasionally runs away.**

Effective persistence is 0.9935 and `E[A(z)^2] = 1.0457`, so the fitted process has no finite unconditional fourth moment, and its implied unconditional volatility (2.94%/day) sits well above the sample volatility (2.34%/day). The consequence is visible directly in the scenarios: about 4% of simulated years are more volatile than anything in the historical record, and the pooled unconditional moments are hostage to a handful of paths — removing the 10 most volatile of 1,000 paths moves pooled excess kurtosis from roughly 390 to 14 against a historical 14.7. `reports/validation_report.md` quantifies this with a leave-out sensitivity table, and `reports/robustness_report.md` shows the pooled moment statistics swinging by several hundred per cent across seeds while the horizon-matched estimates of the same quantities move by ten to twenty per cent.

That instability is reported as a model-risk finding rather than smoothed away. The next experiment would be **GARCH-EVT** if conditional-tail calibration is the priority, or a **regime-aware volatility model** if bounding the explosive paths and fitting the long-memory-like ACF profile matter more. I intentionally stop before those extensions.

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

AI-assisted development is documented in `AIUSAGE.md`, including what was delegated, what remained human review responsibility, and the concrete mistakes that review caught.

`AWS_DESIGN.md` describes an on-demand production path using API Gateway, Lambda, Step Functions, ECS Fargate, ECR, S3, DynamoDB and CloudWatch, including identity/secrets, conditioning state, cost and a 100× usage design.
