"""Risk arithmetic over a generated scenario set.

Every quantity here carries an explicit *kind*, because the single most common
way to mislead with these numbers is to quote a daily figure beside a
horizon figure as though they answered the same question. `MetricKind` forces
that distinction into the type, and the UI prints it next to every value.

VaR and ES delegate to `metrics.var_es`, so the repository's loss convention --
loss = -return, reported as a positive magnitude -- holds here too. Drawdowns
delegate to `metrics.path_max_drawdowns`, which is path-by-path by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from ..metrics import path_max_drawdowns, var_es


class MetricKind(str, Enum):
    """What a number is measured over. Never mix these in one comparison."""

    DAILY = "daily"
    """Estimated from individual days, pooled across paths."""

    TERMINAL = "terminal-horizon"
    """A property of where a path ends, one observation per scenario."""

    PATH = "path-dependent"
    """Depends on the whole trajectory, not just its endpoint."""


@dataclass(frozen=True)
class Metric:
    name: str
    value: float
    kind: MetricKind
    unit: str
    explanation: str


# ---------------------------------------------------------------------------
# Path arithmetic
# ---------------------------------------------------------------------------


def cumulative_log_returns(paths_pct: np.ndarray) -> np.ndarray:
    """Running sum of percentage log returns, shape (n_paths, n_steps).

    Log returns add, which is the whole reason the pipeline works in them.
    """
    return np.cumsum(np.asarray(paths_pct, dtype=float), axis=1)


def terminal_simple_return(paths_pct: np.ndarray) -> np.ndarray:
    """Total simple return over the horizon, as a fraction (0.10 = +10%).

    The paths hold *log* returns in percent. Summing them gives the total log
    return; `expm1` converts that to the simple return an investor would actually
    experience. Reporting the summed log return as though it were a percentage
    gain is a real and easy mistake -- a summed log return of -100 is not a total
    loss, it is a 63% loss.
    """
    total_log = np.sum(np.asarray(paths_pct, dtype=float), axis=1) / 100.0
    return np.expm1(total_log)


def price_paths(paths_pct: np.ndarray, initial_price: float) -> np.ndarray:
    """Reconstruct price paths, including the starting price.

    Returns shape (n_paths, n_steps + 1); column 0 is `initial_price` for every
    path, so a fall on day one is visible rather than being swallowed.
    """
    if initial_price <= 0:
        raise ValueError("initial_price must be positive.")
    x = np.asarray(paths_pct, dtype=float)
    growth = np.exp(np.cumsum(x / 100.0, axis=1))
    start = np.full((x.shape[0], 1), float(initial_price))
    return np.concatenate([start, start * growth], axis=1)


def terminal_prices(paths_pct: np.ndarray, initial_price: float) -> np.ndarray:
    return price_paths(paths_pct, initial_price)[:, -1]


def realized_volatility(paths_pct: np.ndarray, annualize: bool = True) -> np.ndarray:
    """Realized volatility within each path.

    Annualized with the sqrt-of-time rule at 252 trading days, which assumes the
    daily returns are serially uncorrelated. Their *squares* are not, but the
    returns themselves very nearly are, so the rule is reasonable here -- and it
    is an assumption worth naming rather than hiding.
    """
    daily = np.std(np.asarray(paths_pct, dtype=float), axis=1, ddof=1)
    return daily * np.sqrt(252.0) if annualize else daily


def worst_daily_return(paths_pct: np.ndarray) -> np.ndarray:
    """The single worst day within each path, as a log return in percent."""
    return np.min(np.asarray(paths_pct, dtype=float), axis=1)


def simple_from_log(log_return_pct: np.ndarray | float) -> np.ndarray | float:
    """Convert a log return in percent to the simple return a holder experiences.

    The two agree closely for ordinary days -- a -3% log return is a -2.96% simple
    return -- and diverge violently in the tail, which is exactly where a reader
    is most likely to be misled. A log return of -215% is not a 215% loss; nothing
    can fall by more than 100%. It is an 88% loss.
    """
    return np.expm1(np.asarray(log_return_pct, dtype=float) / 100.0) * 100.0


def worst_daily_simple_return(paths_pct: np.ndarray) -> np.ndarray:
    """The worst day within each path, expressed as a simple return."""
    return simple_from_log(worst_daily_return(paths_pct))


def max_drawdowns(paths_pct: np.ndarray) -> np.ndarray:
    """Maximum drawdown per path, via the repository's implementation."""
    return path_max_drawdowns(paths_pct)


# ---------------------------------------------------------------------------
# Probabilities
# ---------------------------------------------------------------------------


def probability_loss_exceeds(paths_pct: np.ndarray, threshold_fraction: float) -> float:
    """P(terminal simple loss worse than `threshold_fraction`).

    `threshold_fraction` is positive for a loss: 0.20 means "loses more than 20%".
    """
    if threshold_fraction < 0:
        raise ValueError("Express the loss threshold as a positive fraction.")
    return float(np.mean(terminal_simple_return(paths_pct) < -threshold_fraction))


def probability_below_initial(paths_pct: np.ndarray) -> float:
    """P(price ends below where it started)."""
    return float(np.mean(terminal_simple_return(paths_pct) < 0.0))


def probability_drawdown_exceeds(paths_pct: np.ndarray, threshold: float) -> float:
    """P(maximum drawdown deeper than `threshold`), threshold as a fraction."""
    return float(np.mean(max_drawdowns(paths_pct) > threshold))


# ---------------------------------------------------------------------------
# Assembled report
# ---------------------------------------------------------------------------

_TERMINAL_PERCENTILES = (1, 5, 25, 50, 75, 95, 99)


def terminal_return_percentiles(paths_pct: np.ndarray) -> dict[int, float]:
    terminal = terminal_simple_return(paths_pct)
    return {q: float(np.percentile(terminal, q)) for q in _TERMINAL_PERCENTILES}


def daily_var_es(paths_pct: np.ndarray, level: float) -> tuple[float, float]:
    """Daily VaR/ES, pooling every simulated day.

    Pooling is legitimate here because the question is about a *day*, and the
    paths supply many days. It would not be legitimate for a statistic whose
    value depends on the sample size, which is why the validation page keeps the
    horizon-matched family separate.
    """
    return var_es(np.asarray(paths_pct, dtype=float).reshape(-1), level)


def terminal_var_es(paths_pct: np.ndarray, level: float) -> tuple[float, float]:
    """VaR/ES of the terminal simple loss, one observation per scenario.

    Reported as positive loss fractions, matching the repository convention.
    """
    return var_es(terminal_simple_return(paths_pct) * 100.0, level)


def risk_report(
    paths_pct: np.ndarray,
    initial_price: float,
    loss_thresholds: tuple[float, ...] = (0.10, 0.20, 0.30),
    drawdown_threshold: float = 0.30,
) -> list[Metric]:
    """Every headline risk number, each tagged with what it is measured over."""
    paths = np.asarray(paths_pct, dtype=float)
    metrics: list[Metric] = []

    for level in (0.95, 0.99):
        v, e = daily_var_es(paths, level)
        pct = int(level * 100)
        metrics.append(
            Metric(
                f"VaR {pct}% (daily)",
                v,
                MetricKind.DAILY,
                "% of value",
                f"On {pct}% of simulated days the loss is no worse than this. "
                "It says nothing about how bad the remaining days get.",
            )
        )
        metrics.append(
            Metric(
                f"ES {pct}% (daily)",
                e,
                MetricKind.DAILY,
                "% of value",
                f"The average loss on the worst {100 - pct}% of simulated days. "
                "Always at least the VaR, and it is the number that describes "
                "severity rather than frequency.",
            )
        )

    for level in (0.95, 0.99):
        v, e = terminal_var_es(paths, level)
        pct = int(level * 100)
        metrics.append(
            Metric(
                f"VaR {pct}% (whole horizon)",
                v,
                MetricKind.TERMINAL,
                "% of value",
                f"Loss over the entire horizon exceeded by only {100 - pct}% of "
                "scenarios. A different question from the daily VaR above.",
            )
        )
        metrics.append(
            Metric(
                f"ES {pct}% (whole horizon)",
                e,
                MetricKind.TERMINAL,
                "% of value",
                f"Average horizon loss across the worst {100 - pct}% of scenarios.",
            )
        )

    dd = max_drawdowns(paths)
    for q, name in ((50, "median"), (95, "p95"), (99, "p99")):
        metrics.append(
            Metric(
                f"Max drawdown ({name})",
                float(np.percentile(dd, q)) * 100.0,
                MetricKind.PATH,
                "% peak-to-trough",
                "Largest fall from a running peak within the horizon. Depends on "
                "the order of the days, not just their distribution.",
            )
        )

    metrics.append(
        Metric(
            "P(ends below start)",
            probability_below_initial(paths) * 100.0,
            MetricKind.TERMINAL,
            "% of scenarios",
            "Share of scenarios finishing below the starting price.",
        )
    )
    for threshold in loss_thresholds:
        metrics.append(
            Metric(
                f"P(loss > {int(threshold * 100)}%)",
                probability_loss_exceeds(paths, threshold) * 100.0,
                MetricKind.TERMINAL,
                "% of scenarios",
                f"Share of scenarios where the horizon loss exceeds "
                f"{int(threshold * 100)}%.",
            )
        )
    metrics.append(
        Metric(
            f"P(drawdown > {int(drawdown_threshold * 100)}%)",
            probability_drawdown_exceeds(paths, drawdown_threshold) * 100.0,
            MetricKind.PATH,
            "% of scenarios",
            "Share of scenarios whose deepest peak-to-trough fall exceeds the "
            "threshold at any point, even if they recover by the end.",
        )
    )

    vol = realized_volatility(paths)
    metrics.append(
        Metric(
            "Realized volatility (median)",
            float(np.median(vol)),
            MetricKind.PATH,
            "% annualized",
            "Volatility actually realized within a scenario, annualized at 252 days.",
        )
    )
    worst = worst_daily_simple_return(paths)
    metrics.append(
        Metric(
            "Worst single day (median)",
            float(np.median(worst)),
            MetricKind.PATH,
            "% simple return",
            "In a typical scenario, the worst day looks like this. Shown as a simple "
            "return: the model works in log returns, which diverge from simple returns "
            "in the tail, and no asset can fall more than 100%.",
        )
    )
    return metrics


# ---------------------------------------------------------------------------
# Monte Carlo convergence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConvergencePoint:
    n_paths: int
    var95: float
    var99: float
    es95: float
    drawdown_p95: float


def convergence_curve(
    generator,
    n_steps: int,
    seed: int,
    path_counts: tuple[int, ...],
    initial_state: str | tuple[float, float],
) -> list[ConvergencePoint]:
    """Recompute headline metrics at increasing path counts.

    Each count is an independent simulation at the same seed, so the sequence
    shows how much of the variation in a reported number is simulation noise.
    It says nothing about whether the model is right: a badly specified model
    converges just as smoothly to the wrong answer.
    """
    points: list[ConvergencePoint] = []
    for n in path_counts:
        paths = generator.simulate(
            n_steps, int(n), seed, initial_state=initial_state
        )
        v95, e95 = daily_var_es(paths, 0.95)
        v99, _ = daily_var_es(paths, 0.99)
        points.append(
            ConvergencePoint(
                n_paths=int(n),
                var95=v95,
                var99=v99,
                es95=e95,
                drawdown_p95=float(np.percentile(max_drawdowns(paths), 95)) * 100.0,
            )
        )
    return points
