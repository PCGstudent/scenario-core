"""Hypothetical return shocks, plus conditional expected variance afterwards."""
from __future__ import annotations

import numpy as np
from .services import variance_step
from .risk import cumulative_return, drawdown_paths, prices

PRESETS = {
    "Single −5%": [-5.], "Single −10%": [-10.],
    "Three consecutive −5%": [-5., -5., -5.],
    "Two-day shock: −8%, −5%": [-8., -5.],
    "Stressed initial volatility": [0.],
}


def experiment(p, shocks_simple_pct, initial_residual, initial_variance, p0, days_after=60):
    """Shocks entered as SIMPLE returns, converted to core percentage log returns.

    After the imposed sequence only E[variance | imposed shocks] is propagated.
    Its square root is not E[volatility]; no post-shock price path is invented.
    """
    simple = np.asarray(shocks_simple_pct, dtype=float)
    if simple.ndim != 1 or not len(simple) or not np.isfinite(simple).all() or (simple <= -100).any():
        raise ValueError("Enter finite simple returns above −100%, separated by commas.")
    if not np.isfinite([initial_residual, initial_variance]).all() or initial_variance <= 0:
        raise ValueError("A finite residual and positive starting variance are required.")
    if not isinstance(days_after, int) or days_after < 1:
        raise ValueError("At least one following day is required.")
    logs = 100 * np.log1p(simple / 100)
    residual, variance = float(initial_residual), float(initial_variance)
    during = []
    for r in logs:
        variance = float(variance_step(p, residual, variance))
        during.append(variance)
        residual = r - p.mu
    after = np.empty(days_after)
    after[0] = variance_step(p, residual, variance)
    persistence = p.effective_persistence
    for t in range(1, days_after):
        after[t] = p.omega + persistence * after[t-1]
    return dict(simple_shocks=simple, log_shocks=logs, during_variance=np.array(during),
                expected_variance_after=after, prices=prices(logs[None, :], p0)[0],
                drawdown=drawdown_paths(logs[None, :])[0],
                terminal_return=float(cumulative_return(logs[None, :])[0, -1]),
                initial_variance=float(initial_variance), initial_residual=float(initial_residual),
                days_after=days_after)


def select(table, criterion, fraction):
    if not 0 < fraction <= 1:
        raise ValueError("The selected fraction must be in (0, 1].")
    columns = {"Terminal loss": ("Terminal return (%)", True),
               "Drawdown": ("Max drawdown (%)", False),
               "Realized volatility": ("Realized volatility (% annualized)", False),
               "Left-tail daily shock": ("Worst daily log return (%)", True)}
    col, ascending = columns[criterion]
    n = max(1, int(np.ceil(len(table) * fraction)))
    return table.sort_values(col, ascending=ascending, kind="stable").head(n)
