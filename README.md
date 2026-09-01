# 4-Xtra Senior DS/MLE Take-Home

A deliberately compact R&D baseline for generating synthetic Brent crude return scenarios.

The submission follows the requested cycle:

**diagnose → choose a model → fit → simulate → validate → report → discuss limitations**

## Canonical clean-clone command

On macOS/Linux:

```bash
bash run.sh
```

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

Both scripts create a local virtual environment, install the exact pinned dependencies from `pyproject.toml`, run tests, and generate the report.

## What the model targets

The implemented baseline is a **GARCH(1,1) model with Student-t innovations** on daily percentage log returns.

It is intended to reproduce:

- heavy-tailed marginal returns;
- time-varying conditional volatility;
- volatility clustering visible through squared-return autocorrelation;
- plausible 1-year loss and drawdown distributions.

It is **not** intended to reproduce:

- structural or geopolitical regime shifts;
- asymmetric volatility responses;
- genuinely unprecedented shocks outside the fitted distribution;
- multivariate dependence with other markets;
- a fully engineered constant-maturity Brent futures series.

Those omissions are deliberate scope choices for a four-hour baseline.

## Data

The code fetches Yahoo Finance ticker `BZ=F` with `yfinance`, using:

- start: `2010-01-01`
- end: `2026-09-01` (exclusive)

The end date is fixed rather than `today()` so the analysis is reproducible.

`BZ=F` is a convenient continuous front-month futures proxy. It is not equivalent to a professionally constructed constant-maturity Brent series, and roll/contract-construction effects may affect observed returns.

Raw market data is never committed.

## Outputs

Running the project produces:

```text
reports/
├── validation_report.md
└── figures/
    ├── diagnostics_acf.png
    ├── tail_qq_student_t.png
    ├── marginal_comparison.png
    ├── squared_acf_real_vs_synthetic.png
    └── drawdown_distribution.png
```

The report contains explicit PASS/FAIL gates for moments, tail quantiles, VaR, Expected Shortfall, squared-return ACF, and 252-trading-day drawdowns.

The thresholds are pragmatic engineering acceptance gates, not formal hypothesis-test significance levels. They are intentionally looser in the far tail, where effective sample size is smaller.

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
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── data.py
│   ├── diagnostics.py
│   ├── metrics.py
│   ├── model.py
│   ├── plots.py
│   ├── report.py
│   └── validation.py
└── tests/
    ├── test_data.py
    ├── test_metrics.py
    └── test_model.py
```

## Statistical interpretation

The generated report automatically inserts the observed empirical moments, return ACF, squared-return ACF, and fitted Student-t degrees of freedom into a concise interpretation.

The intended reasoning is evidence-led:

1. daily returns should exhibit much weaker linear autocorrelation than squared returns;
2. persistent squared-return ACF indicates volatility clustering;
3. excess kurtosis and tail diagnostics indicate that Gaussian innovations are inadequate;
4. GARCH-t is therefore a parsimonious baseline connecting those diagnostics directly to model structure.

## Validation interpretation

Marginal metrics use all simulated observations across independent 252-day paths.

Squared-return ACF is computed **within each path** and then averaged; paths are not concatenated, avoiding artificial boundaries.

Drawdowns are compared like-for-like:

- synthetic maximum drawdown across 252-day simulated paths;
- historical maximum drawdown across rolling 252-trading-day windows.

VaR and Expected Shortfall are reported as **positive loss magnitudes**, with loss defined as `-return`.

## Scope and next model

A likely failure mode is under-representation of the deepest conditional tail or regime-dependent stress.

If that is visible in 99% ES, extreme quantiles, or drawdowns, the next iteration would be a **GARCH-EVT** construction: fit conditional volatility first, then model standardized residual exceedances with Peaks Over Threshold / Generalized Pareto tails. A regime-switching volatility model is another defensible extension.

This submission intentionally stops before either extension unless the diagnostics justify the added complexity.
