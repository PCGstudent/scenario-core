"""Shared copy, glossary and small presentation helpers.

Keeping the wording here rather than scattered through the page code means the
statements the application makes about itself can be reviewed in one place --
which matters, because most of the ways a risk dashboard misleads are wording
choices rather than arithmetic errors.
"""

from __future__ import annotations

# The sentence the whole application exists to keep true.
NOT_A_FORECAST = (
    "This is not a point forecast. The model does not predict the Brent price. "
    "It models the conditional distribution and the volatility dynamics in order "
    "to generate plausible future scenarios for risk analysis."
)

PIPELINE = [
    ("Historical Brent", "Daily close prices fetched in code; nothing is committed."),
    ("Daily log returns", "100 × ln(P_t / P_{t−1}). Log returns add over time."),
    ("Diagnostics", "Moments, autocorrelation, tail index — these choose the model."),
    ("Fit scenario generator", "GJR-GARCH(1,1,1) with skewed-t innovations, by maximum likelihood."),
    ("Current volatility state", "Where the market is now: the last fitted conditional variance."),
    ("Standardized innovations", "Draws from the fitted skewed-t: relative shocks, not returns."),
    ("Recursive volatility evolution", "Each day's variance follows from yesterday's shock and variance."),
    ("Many plausible futures", "Thousands of paths, each internally consistent."),
    ("Risk and stress metrics", "VaR, ES, drawdowns, and stressed subsets."),
]

SEA_ANALOGY = (
    "An analogy, secondary to the mathematics above. The conditional volatility σ_t "
    "is the **state of the sea** — calm or stormy, and it changes slowly. The "
    "standardized innovation z_t is the **relative wave**: how big this particular "
    "wave is compared with what the current sea usually produces. The return shock "
    "is the product of the two, σ_t × z_t. The model does not try to guess the next "
    "wave; it learns how the sea state evolves and what relative waves are possible."
)

GLOSSARY: dict[str, str] = {
    "Return": "The relative change in price. Here always a log return, 100 × ln(P_t/P_{t−1}), "
              "because log returns add over time while percentage changes do not.",
    "Conditional volatility (σ_t)": "The volatility expected for today given everything known "
                                    "up to yesterday. It changes every day — that is the whole "
                                    "point of a GARCH model.",
    "Innovation (ε_t)": "The unexpected part of today's return: ε_t = r_t − μ. It carries the "
                        "size of today's surprise in return units.",
    "Standardized innovation (z_t)": "The innovation divided by the volatility that was expected "
                                     "for that day: z_t = ε_t / σ_t. It is a *relative* shock, not "
                                     "a percentage return. A z of 3 means 'three times the "
                                     "currently expected size', which is a small move in a storm "
                                     "and a large one in a calm.",
    "GARCH": "A model in which today's variance depends on yesterday's squared shock and "
             "yesterday's variance. It produces volatility clustering.",
    "GJR": "An extension in which negative shocks feed the variance more strongly than positive "
           "shocks of the same size, through an extra term γ. This is the leverage effect.",
    "Skewed-t": "The distribution the standardized innovations are drawn from. The parameter η "
                "controls tail heaviness and λ controls asymmetry.",
    "Volatility clustering": "Turbulent days follow turbulent days and calm follows calm. Visible "
                             "as strong autocorrelation in squared returns despite almost none in "
                             "the returns themselves.",
    "VaR": "Value at Risk. The loss exceeded only (1 − level) of the time. It marks the boundary "
           "of the tail and says nothing about what lies beyond it.",
    "ES": "Expected Shortfall. The average loss given that the VaR has been breached. It describes "
          "the severity of the tail rather than only its frequency, and is always at least the VaR.",
    "Drawdown": "The fall from a running peak to a subsequent trough. Path-dependent: it depends "
                "on the order of the days, not just their distribution.",
    "Monte Carlo": "Answering a question by simulating many random outcomes and summarizing them. "
                   "More paths reduce simulation noise; they do not make the model more correct.",
    "Scenario": "One simulated path of returns over the horizon. It is a plausible future under "
                "the fitted model, not a prediction and not something that happened.",
    "Stress test": "An examination of behaviour under severe conditions. Here in two distinct "
                   "forms: filtering the model's own severe draws, which carry a probability, and "
                   "imposing a chosen shock sequence, which carries none.",
    "Horizon-matched validation": "Comparing statistics estimated on equal-length blocks on both "
                                  "sides. Necessary because some statistics depend on sample size, "
                                  "so comparing 252,000 synthetic observations with 4,158 "
                                  "historical ones would measure the sample size, not the model.",
}

# Wording used wherever the interface reports something that cannot be validated.
EXTRAPOLATION_WARNING = (
    "Beyond the worst year in the historical record there is nothing to compare "
    "against. The fitted process has no finite unconditional fourth moment, so the "
    "extrapolation in that region is unusually heavy and is model risk to govern, "
    "not a calibrated statement about Brent."
)

MONTE_CARLO_VS_MODEL_RISK = (
    "Monte Carlo error falls roughly as 1/√N, so more paths make a reported number "
    "steadier. That is simulation noise only. It does not make the model more "
    "correct: a misspecified model converges just as smoothly to the wrong answer."
)

INITIAL_STATE_NOTE = {
    "latest": "Every path starts from the most recent fitted conditional variance — "
              "where the market actually is now. This is the right conditioning for a "
              "forward-looking scenario run.",
    "historical_mix": "Each path starts from a randomly sampled historical fitted state, "
                      "so the set spans the calm and stressed conditions actually "
                      "observed. This is what the validation suite uses, because it makes "
                      "the synthetic distribution comparable with the whole record rather "
                      "than with its final day.",
}


def pass_badge(passed: bool) -> str:
    return "✅ PASS" if passed else "❌ FAIL"
