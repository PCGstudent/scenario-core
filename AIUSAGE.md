# AI-assisted development

I used ChatGPT as an AI coding and reasoning assistant during the take-home. I treated it as a fast collaborator rather than an authority: the assessment requirements were first translated into explicit acceptance criteria, repository invariants, and a small module structure (`AGENTS.md`). From there I used AI primarily for scaffolding, implementation suggestions, test ideas, and adversarial review of statistical choices.

Two concrete workflows were useful. First, a **spec-driven scaffolding loop**: assessment requirement → explicit interface/acceptance criterion → AI draft → manual simplification. That kept the repository intentionally small and avoided an attractive but unjustified neural generator. Second, a **test/review loop**: define a statistical invariant (for example, Expected Shortfall must be at least the corresponding VaR in loss space), have AI propose code/tests, then inspect the output and boundary cases before accepting the change.

I deliberately did not delegate the model choice, sign convention for risk measures, interpretation of diagnostic evidence, validation thresholds, or the decision about what constitutes an honest failure. Those are judgement calls I expect to defend in an interview.

One AI-generated data-loading pattern initially assumed that `yfinance` would always return a flat DataFrame with a direct `"Close"` column. Recent `yfinance` versions can return MultiIndex columns, which would make that implementation brittle. I caught this while reviewing the data boundary rather than trusting the happy path, separated close-price extraction into a tested function, and added coverage for both flat and MultiIndex inputs.

The most valuable use of AI here was therefore not writing more code; it was accelerating iteration while keeping the statistical contract explicit and reviewable.
