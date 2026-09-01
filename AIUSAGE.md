# AI-assisted development

I used ChatGPT as an AI coding and reasoning assistant during the take-home. I treated it as a fast collaborator rather than an authority: the assessment requirements were first translated into explicit acceptance criteria, repository invariants, and a small module structure (`AGENTS.md`). From there I used AI primarily for scaffolding, implementation suggestions, test ideas, and adversarial review of statistical choices.

Two concrete workflows were useful. First, a **spec-driven scaffolding loop**: assessment requirement → explicit interface/acceptance criterion → AI draft → manual simplification. That kept the repository intentionally small and avoided an attractive but unjustified neural generator. Second, a **validation-driven challenger loop**: run the parsimonious baseline, inspect which declared gates fail, propose one targeted refinement, and compare it under exactly the same metrics and thresholds rather than moving the goalposts.

I deliberately kept the risk-measure sign convention and validation thresholds explicit in code rather than allowing the assistant to change them during iteration. I also did not treat any automatic model-selection label as authoritative: AI could propose candidates and comparison logic, but the final rationale had to remain inspectable and defensible from the reported evidence.

A concrete AI mistake appeared after the first challenger run. The initial comparison logic declared the model with the larger number of passed gates the winner, using aggregate normalized error only as a tie-breaker. That looked reasonable before seeing results, but it was too simplistic for a tail-risk problem: the challenger passed more gates while also showing a much worse excess-kurtosis miss and larger aggregate normalized error. Reviewing failure severity exposed the problem, so I rejected the automatic label. The final workflow uses multi-seed robustness, severe-failure counts and analytical fourth-moment diagnostics for both volatility models, and it states where the baseline remains better rather than claiming uniform dominance.

A later review caught another subtlety: `alpha + gamma/2 + beta` is only the symmetric-innovation GJR persistence shortcut. Because the submitted model uses skewed-t innovations, the final code computes the negative-side partial second moment of the fitted distribution explicitly.

The most valuable use of AI was therefore not writing more code; it was accelerating iteration while keeping the statistical contract explicit, testable, and subject to review.
