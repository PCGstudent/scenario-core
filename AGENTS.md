# AGENTS.md

## Objective

Build a small, reproducible Senior DS/MLE take-home solution for Brent crude synthetic scenario generation.

Optimize for **statistical judgement, clarity, reproducibility, and honest validation**, not maximal model complexity.

## Canonical commands

```bash
python -m pytest
python -m xtra_takehome
```

The clean-clone wrapper is:

```bash
bash run.sh
```

## Architecture

- `data.py`: fetch and normalize `BZ=F`; compute percent log returns.
- `diagnostics.py`: moments, ACF, fitted Student-t QQ inputs, mean excess.
- `model.py`: fit GARCH(1,1)-t with `arch`; deterministic in-house simulation from fitted parameters.
- `metrics.py`: VaR, ES, drawdowns, path-level ACF.
- `validation.py`: explicit acceptance gates.
- `plots.py`: deterministic report figures.
- `report.py`: markdown report.
- `__main__.py`: end-to-end orchestration.

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

## Development rules for AI agents

- Read the assessment requirements and this file before editing.
- Prefer minimal changes over broad refactors.
- Add/adjust tests when statistical conventions change.
- Do not silently change metric sign conventions or thresholds.
- Do not introduce neural generators without explicit evidence that the baseline is inadequate and the complexity is justified.
- Run tests after code changes.
- Treat `reports/` as generated output, not hand-edited analysis.
