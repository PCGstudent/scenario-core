# Brent Scenario & Risk Lab

An interactive, educational risk laboratory around the take-home's submitted
**GJR-GARCH(1,1,1) with Hansen skewed-t innovations**. This application is isolated
in this directory; it does not import the separate `src/xtra_takehome/app` work.

Explore eight workspaces: Overview, Historical Data, Model Explainer, Fit &
Validation, Live Scenario Lab, Risk Analysis, Stress Test Lab and optional Ask AI.
Generate 100–10,000 paths over 30–252 trading days; inspect an individual path's
returns, price, cumulative return, conditional volatility and drawdown. Export
scenario summaries, all returns and a reproducibility manifest. Compare Monte
Carlo metrics over five budgets and three independent replications.

## Install and run

Python 3.11 or later. From the repository root:

```powershell
cd brent_risk_lab
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```

On macOS/Linux, replace the last three lines with:

```bash
.venv/bin/python -m pip install -r requirements.txt
source .venv/bin/activate
streamlit run app.py
```

Open **http://127.0.0.1:8517**. The dedicated port avoids collisions with another
Streamlit application in the parent project. Launch from this directory so
Streamlit reads the local theme and server configuration.

If the parent environment already contains the requirements, Windows users can
also run `..\.venv\Scripts\python.exe -m streamlit run app.py` here. No additional
installation is necessary in the environment used for this delivery.

The first load needs Yahoo Finance or the valid existing repository cache. The
data window stays fixed at 2010-01-01 to 2026-09-01 exclusive. **Latest** means the
last fitted observation in this sample, not a live market quote. A fresh download
uses this application's `.cache/`; an existing parent cache is read unchanged.
Data, the fitted generator and diagnostics are cached in memory. The validation
page performs expensive whole-record and multi-seed checks on first opening.

Select settings in the sidebar and click **GENERATE SCENARIOS**. The active run's
settings appear above its results. Form edits take effect only after submission.
Regeneration clears dependent risk, stress, selected-scenario and AI state.

## What this model predicts — and what it does not

It models conditional volatility, a conditional distribution of future returns,
risk scenarios and tail behaviour **under the fitted specification**. GJR learns
the evolution of the sea state (volatility); standardized innovations represent
relative waves; their product produces return shocks.

It does not predict the exact next-day return, the exact future Brent price, or
unprecedented structural changes with certainty. Fan-chart percentiles are
pointwise scenario quantiles, not simultaneous path bands or parameter confidence
intervals. Increasing the path count reduces Monte Carlo noise, not model risk.

## Statistical interpretation

- The core uses **percentage log returns**. Price reconstruction is
  `P0 * exp(cumsum(log_returns_pct / 100))`. Terminal returns are simple percentage
  changes; daily risk retains the core's percentage log-loss convention.
- Every risk metric carries its daily, terminal-horizon or path-dependent scope,
  unit and definition. VaR/ES use `loss = -return`, the core's linear quantiles and
  inclusive tail ties. Negative loss quantiles, if present, denote gains and are
  not silently clipped. ES is at least VaR for this estimator.
- Independent paths are never concatenated to compute ACF. Drawdowns include
  initial wealth at day zero; horizon-matched comparisons use 252 days on both
  sides. Daily pooled risk is over all simulated days in the selected horizon,
  not specifically tomorrow's risk.
- Canonical gates remain fixed at 1,000 paths, 252 days, seed 42 and the historical
  fitted-state mixture. Live runs default to the latest fitted state and do not
  inherit an unconditional calibration PASS as a forecasting endorsement.
- Original single-seed gate verdicts remain visible. Unstable pooled skewness and
  kurtosis values are quoted as medians across seeds 40–49, with ranges, without
  replacing the gates with a new median-based test. The historical comparator is
  retained in the leave-out diagnostic; single-seed higher-moment entries are
  omitted from that UI table.
- The squared-return ACF mismatch is prominent. A model-versus-model Monte Carlo
  difference is not a measure of uncertainty in the historical target. The
  comparison is an in-sample diagnostic, not a formal hypothesis test.
- Stress-region record probabilities preserve the core's `(1-p)^n` independent
  block approximation. Persistent volatility limits that approximation; the UI
  does not present the descriptive flag as a formal gate.
- A deterministic stress input is a **simple** percentage price change. It is
  converted to a percentage log return, and its residual drives the exact GJR
  update. Prices stop at the last imposed return. After that, the displayed
  continuation is `sqrt(E[conditional variance | imposed history])`, **not**
  expected volatility or a forecast price path. Comparators use the same number
  of imposed days; no full-year drawdown is compared with a one-day shock.
- Tail-ranked Monte Carlo subsets are selected by rank. Their selected fraction
  is not an independently estimated probability of a named crisis.

## Optional AI configuration

No key is required for the application. Set server environment variables before
starting Streamlit if you want interpretation:

```powershell
$env:OPENAI_API_KEY = "your-key"
$env:OPENAI_MODEL = "gpt-4.1-mini"
streamlit run app.py
```

The model name is configurable and requires access on your API account. No key
is stored in source, session state or exported context. Do not commit a key.
The integration uses the [OpenAI Responses API](https://developers.openai.com/api/reference/responses/create)
through standard-library HTTPS with a timeout and `store=false`; no SDK is needed.
On explicit question submission, it sends the question and the inspectable
computed summary to OpenAI. It never sends raw history, all Monte Carlo paths or
the API key inside the context. The selected deterministic stress sequence may
be included and is visible in the context preview.

Python computes every metric first. The prompt requires names, units, scopes and
limitations and instructs the assistant to refuse missing numerical values.
Prompt instructions are **not a guarantee against hallucination**: tables and the
exported context remain authoritative. No calculation tools are granted to the
LLM. Tests mock the remote response; a live paid API request is not required by
the test suite.

## Architecture and files

```text
brent_risk_lab/
  app.py                     Eight Streamlit workspaces, caching and navigation
  lab/services.py            Core adapter, fit, provenance, canonical checks, convergence
  lab/risk.py                Risk metrics, price reconstruction and path summaries
  lab/stress.py              Rank filters and deterministic shock propagation
  lab/charts.py              Plotly figures and bounded path display
  lab/state.py               Invalidation after generation
  lab/llm.py                 Structured context serialization and optional API call
  tests/                     Numerical invariants and offline UI interactions
  .streamlit/config.toml     Theme, local-only address and dedicated port
  requirements.txt           Core editable dependency plus pinned UI dependencies
  IMPLEMENTATION_PLAN.md     Reuse and architecture plan
  AUDIT.md                   Statistical and adversarial review
```

The application layer imports the parent's core as a dependency. It does not
alter the original reports, selected model, thresholds, existing app package or
root configuration. `services.fitted_states` is a documented compatibility
boundary reading the core's fitted residual/variance arrays. Generation selects
states on a shallow copy, calls the original simulator, and reconstructs variance
traces using the exact recursion. Regression tests pin core return parity.

## Tests

Run these from this directory after installation:

```bash
python -m pytest
python -m pytest ../tests
```

The isolated tests cover compounding, terminal prices, day-one drawdown, risk
scopes, VaR/ES, threshold counts, deterministic stress, variance timing, RNG
reproducibility, array shapes, pathwise ACF, unchanged fitted state, regeneration,
convergence branching, optional AI and offline Streamlit navigation. The test
suite never requires an API key or a market-data download. No lint/format command
is configured by the original project.

## Limitations and next experiments

The fitted recursion's fourth moment is not finite, and the implied return tail
index is below three. The empirical Hill estimate itself does not prove this for
Brent. Very extreme extrapolations are governed by model assumptions and cannot
be validated from a short history. A stationary univariate model omits regime
changes, parameter uncertainty, joint asset dependence and causal events. Price
paths represent a proxy price index, not roll-adjusted futures P&L. More simulation
does not repair those limitations. An unrepresentable price raises an explicit
error rather than silently clipping scenarios. Browser rendering is limited to
small path samples, while risk computation uses the full set.

Five future improvements:

1. Rolling-origin and regime holdout validation for conditional risk calibration.
2. Parameter uncertainty and repeated-fit sensitivity alongside Monte Carlo error.
3. Governed market-data snapshots and constant-maturity futures construction.
4. Compare regime-aware volatility and GARCH-EVT without automatic model selection.
5. Add authenticated job execution and persistent, versioned scenario artifacts.
