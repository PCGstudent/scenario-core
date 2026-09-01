# AI-assisted development

I used ChatGPT as an AI coding and reasoning assistant during the take-home. I treated it as a fast collaborator rather than an authority: the assessment requirements were first translated into explicit acceptance criteria, repository invariants, and a small module structure (`AGENTS.md`). From there I used AI primarily for scaffolding, implementation suggestions, test ideas, and adversarial review of statistical choices.

Two concrete workflows were useful. First, a **spec-driven scaffolding loop**: assessment requirement → explicit interface/acceptance criterion → AI draft → manual simplification. That kept the repository intentionally small and avoided an attractive but unjustified neural generator. Second, a **validation-driven challenger loop**: run the parsimonious baseline, inspect which declared gates fail, propose one targeted refinement, and compare it under exactly the same metrics and thresholds rather than moving the goalposts.

I deliberately did not delegate the model choice, sign convention for risk measures, interpretation of diagnostic evidence, validation thresholds, or the decision about what constitutes an honest failure. Those are judgement calls I expect to defend in an interview.

A concrete AI mistake occurred during the challenger iteration. The first implementation idea was to rely directly on the `arch` model's high-level simulation path for a skewed-t GJR model. On review, I realised that this made the random-number source and therefore deterministic reproducibility less explicit than I wanted for the assessment. I rejected that shortcut. The final challenger uses an explicitly seeded `arch.univariate.SkewStudent` innovation generator and a transparent GJR variance recursion, with deterministic tests. That review also made the leverage recursion visible enough to inspect rather than hiding it behind a convenience API.

The most valuable use of AI here was therefore not writing more code; it was accelerating iteration while keeping the statistical contract explicit, testable, and reviewable.
