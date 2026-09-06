# Statistical and adversarial UI audit

The review asks: **What claims in this UI could mislead a risk analyst?**

| Risk of misleading interpretation | Resolution / evidence |
|---|---|
| Treating log losses as simple percentage wealth losses | Daily risk uses `% log loss`; endpoints use `% simple loss`. Deterministic stress inputs explicitly use simple returns and convert with `100*log1p(r/100)`. Hand-computed price tests include a −10%, −5% sequence ending at 85.5% of initial wealth. |
| Losing the first day's drawdown | Prices and cumulative paths include day zero. Core maximum drawdown and a log-space drawdown trace are compared in tests. |
| Changing the submitted generator when adding a UI | Monte Carlo returns come from `GjrSkewTGenerator.simulate`. The adapter's historical-mixture output matches the core bit for bit. It selects states on a shallow copy and preserves the shared fitted arrays. |
| Advancing the initial state twice or not at all | A test checks next-day conditional variance against the last fitted residual/variance pair. Trace tests check every later recurrence. |
| Interpreting a standardized innovation as a percentage return | The explainer separately controls σ and z and reports ε = σz. The equal-sign comparison holds residual magnitude and prior variance fixed. |
| Treating the default latest state as a current market feed | Every run records the observed cut-off, and sidebar labels state that this is a fixed sample. Real-data UI verification loaded a last observation of 2026-08-31. |
| Reusing calibration PASS for a short conditional forecast | Canonical validation is computed separately at 252 days and historical-state initialization; live settings never feed its gates. |
| Hiding the ACF failure | The actual canonical run displays FAIL, MAE 0.01911 versus tolerance 0.01800. The model/model Monte Carlo difference, 0.00315, is distinguished from historical sampling uncertainty. |
| Claiming theoretical geometric squared-return ACF when fourth moments do not exist | The UI describes the smoother empirical simulated memory profile, without claiming an existing population squared-return ACF. |
| Quoting one unstable pooled skewness/kurtosis as a model characteristic | Original gate verdicts remain, while displayed higher-moment estimates are medians across seeds 40–49 with ranges. The leave-out UI includes both sources but omits single-seed higher-moment entries. |
| Treating Hill evidence as proof of population moments | Hill charts are explicitly estimates sensitive to k. Fourth-moment existence is attributed to the fitted recursion. The UI distinguishes no fourth moment from no third moment: the latter depends on the fitted tail index being below three. |
| Treating overlapping windows as independent years | The validation page names overlapping windows and the much smaller non-overlapping count. Stress flags are descriptive; the core's independent-block approximation is disclosed. |
| Calling a chosen worst 5% a crisis probability | Tail-selected fractions are explicitly rank selection. They do not estimate the likelihood of a named event. |
| Assigning probabilities or forecast prices to deterministic shocks | Imposed shocks have no assigned probability. Price paths stop after the imposed sequence. Future variance uses the exact fitted lower partial second moment. |
| Calling sqrt(E[variance]) expected volatility | The stress chart labels this distinction explicitly, including the boundary between imposed days and expected-variance continuation. |
| Comparing a one-day stress with full-year drawdown | The Monte Carlo comparator is trimmed to the same imposed-shock duration. One-day realized sample volatility is undefined and returned as NaN, not fabricated as zero. |
| Claiming more Monte Carlo eliminates model uncertainty | The UI distinguishes model risk, historical uncertainty and simulation noise; convergence lines use independent SeedSequence branches and nested whole-path prefixes. No confidence interval is claimed from three replications. |
| Old results appearing to belong to new controls | Generation is form-submitted; active provenance remains visible. New runs invalidate dependent results. Selected scenario IDs persist independently of temporary widgets. AI answers retain their submitted context and show a stale-context notice when appropriate. |
| Letting the LLM invent a missing calculation | Structured Python metrics, numerator/denominator counts, scopes and units are supplied. The prompt refuses missing values and grants no tools. The UI discloses that prompt compliance is not guaranteed and offers the exact context for inspection. |
| Silently clipping extreme scenarios for a prettier chart | Risk computations use all paths. Histograms include the full range, and line rendering selects a bounded subset. Unrepresentable price exponentiation raises an explicit error. |

Validation is an in-sample generative check, not production certification. The
single historical realization and proxy-series construction remain limitations.
No core thresholds, selected model, original reports or original source files
were edited for this isolated application.

## Verification record

- Parent test suite: 114 tests passed; only dependency deprecation warnings.
- Isolated lab: 19 tests passed again on 2026-09-06, including numerical and offline Streamlit interactions across all
  eight pages, canonical validation, generation, stress and regeneration.
- Real-data browser review: overview, canonical validation and scenario/risk
  displays. The loaded model reproduces the reported ACF failure.
- Full convergence experiment completed in the browser: three independent
  10,000-path, 252-day replications, with metrics at all five path budgets.
- Optional API layer: no-key behavior and a mocked Responses API exchange tested.
  No live paid API request was made.
- No lint or formatting tool is configured by the parent project.
