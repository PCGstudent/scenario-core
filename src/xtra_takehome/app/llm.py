"""Optional natural-language layer over already-computed results.

The division of labour is strict and deliberate. Python computes every number;
the model only explains, compares and locates numbers that are already in the
context object. It is never asked to calculate, and the system prompt tells it
to say so rather than invent a figure that is not present.

The application works fully without an API key. Nothing here is imported unless
the page is opened.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, is_dataclass

import numpy as np

ENV_VAR = "OPENAI_API_KEY"
DEFAULT_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You are a risk-analysis assistant embedded in a Brent crude \
scenario-generation application. You explain results that have already been \
computed by the application in Python.

Rules you must follow:

1. Every numerical value in your answer must come from the CONTEXT provided. If a \
number the user asks for is not in the context, say plainly that it has not been \
computed and name what the user would have to run to get it. Never estimate, \
interpolate or invent a figure.

2. Scenarios are not predictions. The model does not forecast the Brent price. It \
models the conditional distribution and the volatility dynamics in order to \
generate plausible futures. Never describe a scenario, a percentile or a fan chart \
as what will happen.

3. Distinguish Monte Carlo uncertainty from model risk. More simulated paths reduce \
simulation noise only. They do not make the model more correct, and you must say so \
if the user implies otherwise.

4. Cite computed metrics by the names used in the context, so the user can find them \
in the interface.

5. State limitations where they are relevant. In particular: the fitted process has \
no finite unconditional fourth moment, so behaviour beyond the historical record is \
extrapolation that cannot be validated against anything; the model is univariate and \
knows nothing about other assets; and the record contains only a limited number of \
non-overlapping years.

6. Be concise and concrete. Prefer a short answer that cites the right metric over a \
long general explanation.

You may explain statistical concepts from general knowledge. You may not produce \
application-specific numbers from general knowledge."""


def api_key_available() -> bool:
    return bool(os.environ.get(ENV_VAR, "").strip())


def _jsonable(value):
    """Make numpy and dataclasses serializable, and keep the payload small."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        # Arrays are summarized, never dumped: the model needs the shape of the
        # evidence, not thousands of raw values.
        flat = value.reshape(-1)
        if flat.size > 12:
            return {
                "_summary": "array",
                "size": int(flat.size),
                "min": float(np.min(flat)),
                "p05": float(np.percentile(flat, 5)),
                "median": float(np.median(flat)),
                "p95": float(np.percentile(flat, 95)),
                "max": float(np.max(flat)),
            }
        return [float(v) for v in flat]
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return str(value)
    return value


@dataclass
class AnalysisContext:
    """Everything the assistant is allowed to reason about."""

    model_parameters: dict
    historical_diagnostics: dict
    validation: dict
    scenario_settings: dict | None = None
    risk_metrics: dict | None = None
    stress_results: dict | None = None
    selected_scenario: dict | None = None

    def to_json(self) -> str:
        payload = {k: _jsonable(v) for k, v in asdict(self).items() if v is not None}
        return json.dumps(payload, indent=2, default=str)

    @property
    def available_sections(self) -> list[str]:
        return [k for k, v in asdict(self).items() if v is not None]


def build_context(
    model, diagnostics, validation, scenarios=None, risk_metrics=None,
    stress=None, selected=None,
) -> AnalysisContext:
    """Assemble the context from application objects, computing nothing new."""
    p = model.params
    s = diagnostics.summary

    model_parameters = {
        "specification": "GJR-GARCH(1,1,1) with Hansen skewed-t innovations",
        "mu": p.mu, "omega": p.omega, "alpha": p.alpha, "gamma": p.gamma,
        "beta": p.beta, "eta_tail_shape": p.eta, "lambda_skew": p.lam,
        "effective_persistence": p.effective_persistence,
        "implied_unconditional_volatility_pct_per_day": model.implied_unconditional_volatility,
        "fourth_moment_coefficient_E_A_squared": p.fourth_moment_coefficient,
        "implied_return_tail_index": p.implied_return_tail_index,
        "latest_conditional_volatility_pct_per_day": model.latest_conditional_volatility,
    }

    historical_diagnostics = {
        "n_observations": s.n, "mean_pct": s.mean, "volatility_pct": s.std,
        "skewness": s.skew, "excess_kurtosis": s.excess_kurtosis,
        "q01": s.q01, "q05": s.q05, "q95": s.q95, "q99": s.q99,
        "max_abs_return_acf": s.max_abs_return_acf,
        "mean_abs_squared_return_acf": s.mean_abs_squared_acf,
        "hill_tail_index_loss_side": s.hill_left,
        "hill_tail_index_gain_side": s.hill_right,
        "student_t_degrees_of_freedom": s.student_t_df,
    }

    validation_payload = {
        "note": "Validation always runs at the canonical configuration "
                "(252-day horizon, 1000 paths, seed 42), because the tolerances "
                "were derived for it. It does not follow the lab controls.",
        "pooled_gates": {g.metric: {"real": g.real, "synthetic": g.synthetic,
                                    "error": g.error, "threshold": g.threshold,
                                    "passed": g.passed}
                         for g in validation.pooled_gates},
        "horizon_matched_gates": {g.metric: {"real_median": g.real,
                                             "synthetic_median": g.synthetic,
                                             "error": g.error, "threshold": g.threshold,
                                             "passed": g.passed}
                                  for g in validation.matched_gates},
        "matched_length_reference": {r.statistic: {"historical": r.historical,
                                                   "model_median": r.model_median,
                                                   "percentile": r.percentile,
                                                   "inside_5_95_band": r.inside}
                                     for r in validation.references},
        "extreme_region": {c.statistic: {
            "worst_observed_year": c.historical_max,
            "model_annual_exceedance_probability": c.annual_exceedance_probability,
            "probability_record_max_below_observed": c.probability_below,
            "flagged": c.flagged} for c in validation.extremes},
        "squared_acf_monte_carlo_floor_median": validation.acf_floor_median,
        "share_of_years_beyond_any_observed": validation.beyond_max_fraction,
    }

    scenario_settings = scenarios.provenance if scenarios is not None else None
    if scenarios is not None:
        scenario_settings = dict(scenario_settings)
        scenario_settings["runtime_seconds"] = scenarios.runtime_seconds

    return AnalysisContext(
        model_parameters=model_parameters,
        historical_diagnostics=historical_diagnostics,
        validation=validation_payload,
        scenario_settings=scenario_settings,
        risk_metrics=risk_metrics,
        stress_results=stress,
        selected_scenario=selected,
    )


def metrics_payload(metrics) -> dict:
    """Turn the risk report into a flat, citable mapping."""
    return {
        m.name: {"value": m.value, "unit": m.unit, "measured_over": m.kind.value}
        for m in metrics
    }


def ask(question: str, context: AnalysisContext, model_name: str = DEFAULT_MODEL,
        timeout: float = 60.0) -> str:
    """Send one question with the context. Raises if no key is configured."""
    key = os.environ.get(ENV_VAR, "").strip()
    if not key:
        raise RuntimeError(
            f"No {ENV_VAR} in the environment. The rest of the application works "
            "without it; only this page needs a key."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "The 'openai' package is not installed. Install it with "
            "`pip install openai` to enable this page."
        ) from exc

    client = OpenAI(api_key=key, timeout=timeout)
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": f"CONTEXT (the only source of application numbers):\n"
                        f"{context.to_json()}\n\nQUESTION: {question}"},
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content or ""


SUGGESTED_QUESTIONS = [
    "Explain the current VaR 99% in simple terms.",
    "Why is ES 99% materially worse than VaR 99%?",
    "Does increasing paths from 1,000 to 10,000 reduce model uncertainty?",
    "What is the main weakness of this model?",
    "What happens to volatility after a large negative shock?",
    "Compare the simulated drawdown distribution with history.",
    "Which validation gates fail, and what does each failure mean?",
]
