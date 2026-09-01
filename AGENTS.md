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
- `validation.py`: shared tolerances and scale-derived ones; pooled gates, horizon-matched gates, matched-length reference, extreme-region plausibility, leave-out sensitivity.
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
12. Do not tune a tolerance to make a model pass. A tolerance may be corrected after results are known **only** when it is shown to be defective — not measuring what it claims — and every such correction must be disclosed in the report along with the direction of its effect. The squared-return ACF tolerance was rescaled on exactly those grounds, and the correction makes the submitted model fail that gate rather than pass it.
13. Estimate every comparison statistic on equal-length blocks on **both** sides before calling a gate a model failure. A statistic that is sample-size dependent (squared-return ACF, and sample kurtosis when the fourth moment does not exist) measures the estimator, not the generator, when the two samples differ in size.
14. **A gate that nothing can fail is not a gate.** Every gate needs a test proving it rejects a generator that lacks the property it claims to test. This is not hypothetical: carrying an absolute squared-return ACF tolerance from the pooled estimator into the block estimator, where the historical target is roughly three times smaller, produced a gate that an iid bootstrap of the real returns passed.
15. Scale-free tolerances live in `validation.THRESHOLDS` and are shared by both families. A tolerance that needs a scale (`mean return`, `squared-return ACF MAE`) must be **re-derived from the historical sample under the estimator in use**. Holding an absolute number constant across estimators is a silent relaxation, not neutrality.
16. Do not gate on an upper quantile of the historical block distribution. Overlapping windows make the top few per cent one episode repeated, so the quantile is not identified. Report it instead.
17. Never compare a maximum across different sample sizes. The maximum of a heavy-tailed sample grows with the sample, so `max(simulated)` against `max(observed)` measures the simulation budget. Project the model onto a record of the same length as the historical one.
18. A diagnostic that appears to convict the model must carry the historical comparator. The leave-out table collapses on historical data too; without that row it would present a universal property of heavy-tailed samples as a defect.
19. Do not report a scalar aggregate score across gates. Summing error/threshold ratios lets one unbounded statistic dominate, and capping the ratio only substitutes an arbitrary constant. Pass counts, the metric table and the structural diagnostics carry the information.
20. Quote statistics that have no finite population value (pooled kurtosis and skewness here) as a median across seeds, never from a single realization.
21. Derive simulation streams from `SeedSequence(seed).spawn(...)`, never `seed + 1`, so replications do not share a generator stream.
22. Figures are written, never displayed: select a non-interactive matplotlib backend.

## Development rules for AI agents

- Read the assessment requirements and this file before editing.
- Prefer minimal, evidence-driven changes over broad refactors.
- Add/adjust tests when statistical conventions change.
- Do not silently change metric sign conventions, initialization rules, thresholds or the selected model.
- When a gate fails, diagnose the mechanism before reporting it. "It fails and I keep the failure" is not a finding; naming what in the fit produces it is.
- Before claiming a model failure, check whether the historical sample would show the same thing under the same measurement. Most of the apparent failures here did.
- Prefer a check the data can answer over a formal-looking one it cannot. The stressed region carries no PASS/FAIL because sixteen non-overlapping blocks do not support one.
- Do not introduce neural generators without explicit evidence that the interpretable volatility models are inadequate and that the extra complexity is justified.
- Run tests after code changes.
- Treat `reports/` as generated output, not hand-edited analysis.
- Preserve honest limitations; never optimize prose or gates to conceal a model-risk finding.
