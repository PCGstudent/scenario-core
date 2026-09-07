"""Two kinds of stress, kept apart on purpose.

**Model-generated stress** filters the Monte Carlo scenarios the fitted model
already produced. Those paths carry a probability: they are draws from the
fitted distribution, and the fraction of them that is severe is a statement
about how often the model thinks such a year happens.

**Deterministic stress** propagates a shock sequence the user invented. Those
paths carry *no* probability at all. The model is being asked a conditional
question -- "if these returns occurred, what would the volatility recursion do
next?" -- and the answer is a mechanical consequence of the fitted parameters,
not evidence about likelihood.

Confusing the two is the classic way a stress-test dashboard misleads, so the
return types are different and each carries its own interpretation string.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..challenger import GjrSkewTParams
from .risk import max_drawdowns, realized_volatility, terminal_simple_return, worst_daily_return


# ---------------------------------------------------------------------------
# Mode A -- filtering the model's own scenarios
# ---------------------------------------------------------------------------

FILTERS = {
    "worst 1% by terminal loss": ("terminal", 0.01),
    "worst 5% by terminal loss": ("terminal", 0.05),
    "deepest 5% drawdowns": ("drawdown", 0.05),
    "highest 5% realized volatility": ("volatility", 0.05),
    "largest 5% single-day shocks": ("shock", 0.05),
}


def filter_scenarios(paths_pct: np.ndarray, criterion: str) -> np.ndarray:
    """Indices of the scenarios matching a severity criterion.

    These remain draws from the fitted model, so the share selected is the
    model's own estimate of how common such a year is.
    """
    if criterion not in FILTERS:
        raise ValueError(f"Unknown filter {criterion!r}; expected one of {list(FILTERS)}")
    kind, fraction = FILTERS[criterion]
    paths = np.asarray(paths_pct, dtype=float)
    n = max(1, int(round(fraction * paths.shape[0])))

    if kind == "terminal":
        score = terminal_simple_return(paths)
        return np.argsort(score)[:n]
    if kind == "drawdown":
        return np.argsort(max_drawdowns(paths))[::-1][:n]
    if kind == "volatility":
        return np.argsort(realized_volatility(paths))[::-1][:n]
    return np.argsort(worst_daily_return(paths))[:n]


# ---------------------------------------------------------------------------
# Mode B -- deterministic shock experiments
# ---------------------------------------------------------------------------

PRESETS: dict[str, list[float]] = {
    "single -5% day": [-5.0],
    "single -10% day": [-10.0],
    "three consecutive -5% days": [-5.0, -5.0, -5.0],
    "2020-style week: -8, -5, +4, -6, -3": [-8.0, -5.0, 4.0, -6.0, -3.0],
    "single +10% day (for comparison)": [10.0],
}


@dataclass(frozen=True)
class StressExperiment:
    """The consequence of an assumed shock sequence. Not a forecast, not a draw."""

    shocks_pct: np.ndarray
    """The returns the user imposed, in percent."""

    conditional_volatility: np.ndarray
    """Volatility the recursion produces on each imposed day, percent per day."""

    volatility_after: np.ndarray
    """Expected volatility decay over the days following the shock sequence."""

    starting_volatility: float
    peak_volatility: float
    days_to_half_decay: int | None

    @property
    def volatility_multiple(self) -> float:
        """How many times the starting volatility the shock produced at its peak."""
        return float(self.peak_volatility / self.starting_volatility)

    @property
    def interpretation(self) -> str:
        return (
            "This is a conditional experiment, not a simulation draw and not a "
            "forecast. It answers: if exactly these returns occurred, what would "
            "the fitted volatility recursion do next? No probability attaches to "
            "the shock sequence itself -- the user chose it."
        )


def propagate_shocks(
    params: GjrSkewTParams,
    shocks_pct: list[float] | np.ndarray,
    initial_variance: float,
    initial_residual: float = 0.0,
    days_after: int = 60,
) -> StressExperiment:
    """Push an assumed return sequence through the GJR variance recursion.

    The recursion is exactly the one in `challenger.simulate`; only the source of
    the shock changes -- imposed here, drawn from the innovation law there.

    After the imposed days, the decay is shown with the *expected* multiplier
    under the fitted innovation law, `E[A(z)] = effective persistence`. That is
    the mean path, not a simulated one, so no randomness enters the tail of the
    picture and it cannot be mistaken for a scenario.
    """
    shocks = np.asarray(shocks_pct, dtype=float).reshape(-1)
    if shocks.size == 0:
        raise ValueError("Provide at least one shock.")
    if initial_variance <= 0:
        raise ValueError("initial_variance must be positive.")
    if days_after < 0:
        raise ValueError("days_after must not be negative.")

    variance = float(initial_variance)
    residual = float(initial_residual)
    starting_volatility = float(np.sqrt(variance))

    during = np.empty(shocks.size, dtype=float)
    for i, shock in enumerate(shocks):
        leverage = 1.0 if residual < 0.0 else 0.0
        variance = (
            params.omega
            + params.alpha * residual**2
            + params.gamma * leverage * residual**2
            + params.beta * variance
        )
        variance = max(variance, 1e-12)
        during[i] = np.sqrt(variance)
        # The imposed return becomes the residual that drives the next day.
        residual = float(shock) - params.mu

    # Expected decay: one more step with the realized residual, then the mean
    # multiplier repeatedly. Deterministic by construction.
    after = np.empty(days_after, dtype=float)
    persistence = params.effective_persistence
    long_run = (
        params.omega / (1.0 - persistence) if persistence < 1.0 else float("inf")
    )
    v = variance
    r = residual
    for i in range(days_after):
        if i == 0:
            # The last imposed return is a realized residual, so this step uses
            # the exact recursion including the leverage term.
            leverage = 1.0 if r < 0.0 else 0.0
            v = (
                params.omega
                + params.alpha * r**2
                + params.gamma * leverage * r**2
                + params.beta * v
            )
        elif np.isfinite(long_run):
            # Mean reversion toward the long-run level at the expected rate.
            v = long_run + persistence * (v - long_run)
        else:
            # No finite long-run level: the expected step is the raw recursion.
            v = params.omega + persistence * v
        v = max(v, 1e-12)
        after[i] = np.sqrt(v)

    peak = float(max(during.max(), after[0] if days_after else during.max()))
    half = None
    if days_after:
        target = starting_volatility + 0.5 * (peak - starting_volatility)
        below = np.flatnonzero(after <= target)
        half = int(below[0]) + 1 if below.size else None

    return StressExperiment(
        shocks_pct=shocks,
        conditional_volatility=during,
        volatility_after=after,
        starting_volatility=starting_volatility,
        peak_volatility=peak,
        days_to_half_decay=half,
    )


def asymmetry_example(
    params: GjrSkewTParams, magnitude: float, current_variance: float
) -> dict[str, float]:
    """Next-day variance after an equal-sized down and up move.

    This is the clearest way to see what gamma buys: identical magnitudes, and
    the recursion still treats them differently.
    """
    if magnitude <= 0:
        raise ValueError("magnitude must be positive.")
    base = params.omega + params.beta * current_variance
    shock_sq = magnitude**2
    down = base + (params.alpha + params.gamma) * shock_sq
    up = base + params.alpha * shock_sq
    return {
        "variance_after_down": float(down),
        "variance_after_up": float(up),
        "volatility_after_down": float(np.sqrt(down)),
        "volatility_after_up": float(np.sqrt(up)),
        "extra_variance_from_leverage": float(down - up),
        "ratio": float(np.sqrt(down) / np.sqrt(up)),
    }
