# Brent Scenario & Risk Lab — implementation plan

The user requested an isolated application directory after inspection revealed
another implementation changing files in the original application package. All
files for this delivery therefore live under `brent_risk_lab/`.

1. Existing pipeline: fixed-window BZ=F closes → percentage log returns → tail and
   dependence diagnostics → fitted GJR-GARCH(1,1,1) / Hansen skewed-t → independent
   fitted-state scenario paths → pooled and 252-day matched gates → matched-length
   records, stress-region diagnostics and multi-seed robustness → reports.
2. Reuse: import `data`, `diagnostics`, `challenger`, `metrics`, `windows` and
   `validation` directly from the existing core. Preserve fitted parameters,
   innovation draws, seeds, thresholds, drawdown initialization and model selection.
3. Services: application-local orchestration for cached inputs/fit, generation
   metadata, a conditional variance trace, canonical validation and convergence.
   Core simulation remains the source of every Monte Carlo return. An adapter
   selects initial fitted states on a shallow copy, without changing shared fits.
   No refactor of the original source files is required.
4. Architecture: `app.py` provides eight Streamlit workspaces; `lab/services.py`
   handles orchestration; `risk.py`, `stress.py`, `charts.py`, `state.py`, and
   `llm.py` hold reusable computation, rendering, transitions and interpretation.
   Service functions have no Streamlit imports and can support a future API.
5. Verify: independent risk and stress identities, core parity, seed/shape/ACF
   tests, offline Streamlit interactions, original tests, real-data local launch,
   browser chart review and an adversarial interpretation audit.
