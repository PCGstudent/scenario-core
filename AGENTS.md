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

- `data.py`: fetch/normalize `BZ=F` with a gitignored local cache; percentage log returns.
- `diagnostics.py`: moments, ACF, Student-t QQ inputs, Hill tail index, mean excess.
- `model.py`: development GARCH(1,1)-t baseline and deterministic fitted-state simulation.
- `challenger.py`: submitted GJR-GARCH(1,1,1)-skew-t generator; exact skew-t persistence, implied unconditional variance and fourth-moment diagnostics.
- `metrics.py`: VaR, ES, drawdowns, path-level squared-return ACF.
- `windows.py`: year-level block statistics; the horizon-matched estimator used on both sides.
- `validation.py`: one declared threshold table; pooled gates, horizon-matched gates, worst-year exceedance checks, explosive-path sensitivity.
- `compare_models.py`: same-seed baseline/challenger development comparison; never auto-selects a winner.
- `robustness.py`: 10-seed stability of both estimator families and higher-moment analysis.
- `plots.py`: deterministic report figures (non-interactive Agg backend).
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
13. Estimate every comparison statistic on equal-length blocks on **both** sides before calling a gate a model failure. A statistic that is sample-size dependent (squared-return ACF, and sample kurtosis when the fourth moment does not exist) measures the estimator, not the generator, when the two samples differ in size.
14. Thresholds live in exactly one place, `validation.THRESHOLDS`, shared by both gate families. Changing an estimator must never change a tolerance, and the two families must remain scoreable side by side.
15. Do not gate on an upper quantile of the historical block distribution. Overlapping windows make the top few per cent one episode repeated, so that quantile is not identified. Use an exceedance frequency against the number of **independent** blocks instead.
16. Derive simulation streams from `SeedSequence(seed).spawn(...)`, never `seed + 1`, so replications do not share a generator stream.
17. Any aggregate score must cap the per-gate error ratio. An unbounded statistic must never be able to dominate a model ranking.
18. Figures are written, never displayed: select a non-interactive matplotlib backend.

## Development rules for AI agents

- Read the assessment requirements and this file before editing.
- Prefer minimal, evidence-driven changes over broad refactors.
- Add/adjust tests when statistical conventions change.
- Do not silently change metric sign conventions, initialization rules, thresholds or the selected model.
- When a gate fails, diagnose the mechanism before reporting it. "It fails and I keep the failure" is not a finding; naming what in the fit produces it is.
- Do not introduce neural generators without explicit evidence that the interpretable volatility models are inadequate and that the extra complexity is justified.
- Run tests after code changes.
- Treat `reports/` as generated output, not hand-edited analysis.
- Preserve honest limitations; never optimize prose or gates to conceal a model-risk finding.
