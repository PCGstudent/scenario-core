# AGENTS.md

## Objective

Build a small, reproducible Senior DS/MLE take-home solution for Brent crude synthetic scenario generation.

Optimize for **statistical judgement, clarity, reproducibility, and honest validation**, not maximal model complexity.

The submitted generator is **GJR-GARCH(1,1,1) with Hansen skewed-t innovations**. The symmetric GARCH-t implementation is retained only as a development baseline and comparison point.

## Canonical commands

```bash
python -m pytest
python -m xtra_takehome
```

Supporting development analyses:

```bash
python -m xtra_takehome.compare_models
python -m xtra_takehome.robustness
```

Clean-clone wrappers:

```bash
bash run.sh
# or Windows: powershell -ExecutionPolicy Bypass -File .\run.ps1
```

## Architecture

- `data.py`: fetch/normalize `BZ=F`; compute percentage log returns.
- `diagnostics.py`: moments, ACF, Student-t QQ inputs, mean excess.
- `model.py`: development GARCH(1,1)-t baseline and deterministic fitted-state simulation.
- `challenger.py`: submitted GJR-GARCH(1,1,1)-skew-t generator; exact skew-t persistence/fourth-moment diagnostics.
- `metrics.py`: VaR, ES, drawdowns, path-level squared-return ACF.
- `validation.py`: fixed acceptance gates.
- `compare_models.py`: same-seed baseline/challenger development comparison; never auto-selects a winner.
- `robustness.py`: 10-seed stability, severe-failure and higher-moment analysis.
- `plots.py`: deterministic report figures.
- `report.py`: markdown report for the submitted model.
- `__main__.py`: final end-to-end orchestration.

## Statistical invariants

1. Model **returns**, not price levels.
2. Returns are percentage log returns: `100 * log(P_t/P_{t-1})`.
3. Do not winsorize or remove observations merely because they are extreme.
4. VaR/ES use loss `L = -return` and are positive loss magnitudes.
5. Never concatenate independent synthetic paths before computing ACF.
6. Drawdown comparisons must use equal 252-trading-day horizons.
7. Keep the data end date fixed for reproducibility.
8. Every stochastic operation must be seed-controlled.
9. A FAIL is evidence, not a defect to hide.
10. Do not select a model from PASS count alone; inspect failure severity, multi-seed stability and structural diagnostics.
11. For skewed GJR innovations, do not use `gamma/2` as exact persistence; use the fitted distribution's lower partial second moment.
12. Do not change validation thresholds after seeing model results.

## Development rules for AI agents

- Read the assessment requirements and this file before editing.
- Prefer minimal, evidence-driven changes over broad refactors.
- Add/adjust tests when statistical conventions change.
- Do not silently change metric sign conventions, initialization rules, thresholds or the selected model.
- Do not introduce neural generators without explicit evidence that the interpretable volatility models are inadequate and that the extra complexity is justified.
- Run tests after code changes.
- Treat `reports/` as generated output, not hand-edited analysis.
- Preserve honest limitations; never optimize prose or gates to conceal a model-risk finding.
