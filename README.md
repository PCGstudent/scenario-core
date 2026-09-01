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

Both wrappers create `.venv`, install the exact pinned dependencies from `pyproject.toml`, run the test suite, and generate the final validation report and figures.

For the supporting model-selection analyses:

```bash
python -m xtra_takehome.compare_models
python -m xtra_takehome.robustness
```

These regenerate `reports/model_comparison.md` and `reports/robustness_report.md`.

## Data and return definition

The code fetches Yahoo Finance Brent ticker `BZ=F` with `yfinance`:

- start: `2010-01-01`
- end: `2026-09-01` (exclusive)
- return: `100 * log(P_t / P_{t-1})`

The end date is fixed rather than `today()` so a clean clone has a reproducible analysis window. Raw market data is not committed.

`BZ=F` is a convenient continuous/front-month proxy, not a professionally engineered constant-maturity Brent series. Roll and contract-construction effects may therefore contaminate some observed returns; this is treated as a data limitation rather than silently ignored.

## Why this model

Diagnostics show three features that drive the modelling decision:

1. weak linear dependence in daily returns;
2. materially stronger autocorrelation in squared returns, indicating volatility clustering;
3. negative skew and heavy tails, inconsistent with Gaussian iid Monte Carlo.

I first fitted **GARCH(1,1)-Student-t** because it is the smallest model that directly targets volatility clustering plus heavy tails. Validation then exposed a material asymmetry miss: the symmetric baseline did not reproduce historical negative skew reliably and overstated several positive-tail quantiles. I therefore made one targeted refinement rather than jumping to a neural generator: **GJR-GARCH + skewed-t**. GJR adds sign-dependent volatility response; Hansen's skewed-t adds conditional skew while retaining heavy tails.

The challenger was not selected from one favourable seed. `reports/robustness_report.md` compares both models over seeds 40–49 using identical data, horizons, path counts, initialization principles, metrics and gates. It also reports severe failures and analytical higher-moment diagnostics. This was added after rejecting an overly simplistic AI-assisted rule that initially equated “more PASS gates” with “better model.”

## What the submitted model targets

It is intended to reproduce:

- heavy-tailed daily return behaviour;
- negative return asymmetry;
- time-varying conditional volatility;
- volatility clustering;
- plausible one-year tail-loss and drawdown distributions.

It is **not** intended to reproduce:

- causal geopolitical mechanisms or genuinely unprecedented shocks;
- structural/regime changes with a single stationary recursion;
- multivariate dependence with rates, FX, equities or other commodities;
- a professionally constructed constant-maturity Brent futures curve.

Those are deliberate scope boundaries, not implicit claims of adequacy.

## Validation

The final generator is checked against historical returns on:

- mean, volatility, skewness and excess kurtosis;
- 1%, 5%, 95% and 99% return quantiles;
- VaR and Expected Shortfall at 95% and 99%;
- squared-return ACF over lags 1–20;
- maximum drawdown over 252-trading-day horizons.

VaR and ES use **loss `L = -return`** and are reported as positive loss magnitudes.

Marginal metrics pool observations across independent simulated paths. Squared-return ACF is deliberately calculated **within each path and then averaged**; independent paths are never concatenated. Drawdowns are compared like-for-like: synthetic 252-day paths versus historical rolling 252-day windows.

Acceptance thresholds are pragmatic engineering gates rather than hypothesis-test significance levels. Far-tail tolerances are wider because effective sample size is smaller. Thresholds are fixed in code and FAILs are retained rather than tuned away.

## Model-risk finding

Higher moments remain the clearest limitation. The project evaluates the fourth-moment condition analytically rather than treating sample kurtosis as a stable target by assumption. For the skewed GJR model, effective persistence is computed using the fitted innovation law,

`alpha + beta + gamma * E[z^2 I(z<0)]`,

rather than the symmetric `gamma/2` shortcut. The robustness report also evaluates the GJR fourth-moment coefficient `E[A(z)^2]` directly. Remaining higher-moment and squared-ACF failures are discussed explicitly in the final report.

The next experiment would be **GARCH-EVT** if conditional-tail calibration is the priority, or a **regime-aware volatility model** if state-dependent persistence is the main residual failure. I intentionally stop before those extensions.

## Outputs

```text
reports/
├── validation_report.md        # final submitted model
├── model_comparison.md         # development baseline vs challenger
├── robustness_report.md        # 10-seed stability/model-risk analysis
└── figures/
    ├── diagnostics_acf.png
    ├── tail_qq_student_t.png
    ├── marginal_comparison.png
    ├── squared_acf_real_vs_synthetic.png
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
│   └── validation.py
└── tests/
```

## Reproducibility and AI use

Every stochastic operation is seed-controlled. The fitted model uses an explicit fit-then-simulate interface, and simulation starts from sampled historical fitted residual/variance states so paths cover empirically observed calm and stressed initial conditions.

AI-assisted development is documented in `AIUSAGE.md`, including what was delegated, what was deliberately kept as human judgement, and a concrete AI-generated model-selection mistake that was detected and corrected through review.

`AWS_DESIGN.md` describes an on-demand production path using API Gateway, Lambda, Step Functions, ECS Fargate, ECR, S3, DynamoDB and CloudWatch, including identity/secrets, cost and a 100× usage design.
