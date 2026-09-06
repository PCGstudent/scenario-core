"""Deterministic risk arithmetic; log returns in percent, simple returns in percent.

The core VaR/ES and pathwise drawdown conventions are deliberately reused.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from xtra_takehome.metrics import path_max_drawdowns, var_es


def checked_paths(paths):
    x = np.asarray(paths, dtype=float)
    if x.ndim != 2 or min(x.shape) < 1 or not np.isfinite(x).all():
        raise ValueError("Expected a nonempty finite (scenarios, days) return array.")
    return x


def cumulative_return(paths):
    """Simple cumulative return (%), including day zero; never sum simple returns."""
    x = checked_paths(paths)
    logs = np.column_stack((np.zeros(len(x)), np.cumsum(x / 100, axis=1)))
    with np.errstate(over="raise", invalid="raise"):
        try:
            return np.expm1(logs) * 100
        except FloatingPointError as exc:
            raise ValueError("A scenario exceeds floating-point price range; no paths were clipped.") from exc


def prices(paths, p0):
    if not np.isfinite(p0) or p0 <= 0:
        raise ValueError("Starting price must be finite and positive.")
    # exp, rather than 1 + expm1, avoids cancellation for very large losses.
    x = checked_paths(paths)
    logs = np.column_stack((np.zeros(len(x)), np.cumsum(x / 100, axis=1)))
    with np.errstate(over="raise", invalid="raise"):
        try:
            return p0 * np.exp(logs)
        except FloatingPointError as exc:
            raise ValueError("A scenario exceeds floating-point price range; no paths were clipped.") from exc


def drawdown_paths(paths):
    """Drawdown in %, computed in log space including initial wealth."""
    x = checked_paths(paths)
    logs = np.column_stack((np.zeros(len(x)), np.cumsum(x / 100, axis=1)))
    return -100 * np.expm1(logs - np.maximum.accumulate(logs, axis=1))


def path_table(paths, p0):
    x = checked_paths(paths)
    return pd.DataFrame({
        "Scenario ID": np.arange(len(x)),
        "Terminal return (%)": cumulative_return(x)[:, -1],
        "Terminal price (USD/bbl)": prices(x, p0)[:, -1],
        "Max drawdown (%)": path_max_drawdowns(x) * 100,
        "Realized volatility (% annualized)": (x.std(axis=1, ddof=1) * np.sqrt(252)
                                                if x.shape[1] > 1 else np.full(len(x), np.nan)),
        "Worst daily log return (%)": x.min(axis=1),
    })


def probability(values, threshold):
    """Empirical P(value > threshold), with numerator and Monte Carlo denominator."""
    a = np.asarray(values, dtype=float)
    if a.size == 0 or not np.isfinite(a).all() or not np.isfinite(threshold):
        raise ValueError("Finite values and threshold are required.")
    count = int(np.count_nonzero(a > threshold))
    return {"count": count, "n": int(a.size), "probability_pct": 100 * count / a.size}


def metrics(paths, p0, loss_threshold=25.0, drawdown_threshold=30.0):
    """Each numerical metric carries a scope, unit and human explanation."""
    x = checked_paths(paths)
    table = path_table(x, p0)
    terminal = table["Terminal return (%)"].to_numpy()
    rows = []

    def add(name, value, scope, unit, explanation):
        rows.append(dict(metric=name, value=float(value), scope=scope, unit=unit, explanation=explanation))

    for scope, values, unit in (("Daily (all simulated days)", x.reshape(-1), "% log loss"),
                                ("Terminal horizon", terminal, "% simple loss")):
        for level in (.95, .99):
            v, e = var_es(values, level)
            add(f"{scope}: VaR{int(level*100)}", v, scope, unit,
                "Empirical loss quantile (loss = minus return); exceeded in approximately the remaining tail. "
                "Linear quantile interpolation and ties can change the exact exceedance count. Negative values denote gains.")
            add(f"{scope}: ES{int(level*100)}", e, scope, unit,
                "Average empirical loss at or above VaR; includes ties, as in the core implementation.")
    for q in (1, 5, 25, 50, 75, 95, 99):
        add(f"Terminal return p{q}", np.percentile(terminal, q), "Terminal horizon", "% simple return",
            "Percentile across scenario endpoints; one observation per path.")
    for threshold in sorted({0., 10., 20., 30., float(loss_threshold)}):
        event = probability(-terminal, threshold)
        add(f"P(terminal loss > {threshold:g}%)", event["probability_pct"], "Terminal horizon", "% of scenarios",
            f"{event['count']} of {event['n']} simulated paths. Strict exceedance; model-conditional frequency.")
    dd = table["Max drawdown (%)"].to_numpy()
    for q in (50, 95, 99):
        add(f"Maximum drawdown p{q}", np.percentile(dd, q), "Path-dependent", "% peak-to-trough loss",
            "Percentile of each path's greatest fall from its running high, including day zero.")
    event = probability(dd, drawdown_threshold)
    add(f"P(drawdown > {drawdown_threshold:g}%)", event["probability_pct"], "Path-dependent", "% of scenarios",
        f"{event['count']} of {event['n']} paths cross this drawdown, even if they later recover.")
    for col in ("Realized volatility (% annualized)", "Worst daily log return (%)"):
        for q in (5, 50, 95):
            add(f"{col} p{q}", np.percentile(table[col], q), "Path-dependent", "%",
                "Computed within each path. Volatility uses sample standard deviation × sqrt(252); "
                "annualization assumes negligible return autocorrelation, not independent squared returns.")
    return pd.DataFrame(rows)
