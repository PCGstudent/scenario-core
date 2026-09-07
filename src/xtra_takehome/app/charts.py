"""Plotly figures for the lab.

Two rules run through this module. First, never draw thousands of opaque lines:
a fan chart of percentiles carries the distribution, and a small sample of paths
carries the texture. Second, keep the palette restrained -- one accent for the
model, one neutral for history, and semantic colour reserved for pass/fail and
for the stressed subset.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

# One accent, one neutral, semantic colours used only where they mean something.
TEAL = "#0E6B63"
TEAL_FILL = "rgba(14, 107, 99, {alpha})"
SAND = "#B0761A"
SAND_FILL = "rgba(176, 118, 26, {alpha})"
GOOD = "#3D7A4E"
BAD = "#AC3A2E"
GRID = "rgba(128,138,140,0.18)"

_LAYOUT = dict(
    template="plotly_white",
    margin=dict(l=60, r=24, t=48, b=48),
    hovermode="x unified",
    font=dict(family="Source Sans 3, system-ui, sans-serif", size=13),
    xaxis=dict(gridcolor=GRID, zerolinecolor=GRID),
    yaxis=dict(gridcolor=GRID, zerolinecolor=GRID),
    legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
)


def _style(fig: go.Figure, title: str, x: str, y: str, height: int = 420) -> go.Figure:
    fig.update_layout(**_LAYOUT, title=dict(text=title, x=0, font=dict(size=15)), height=height)
    fig.update_xaxes(title_text=x)
    fig.update_yaxes(title_text=y)
    return fig


# ---------------------------------------------------------------------------
# Historical
# ---------------------------------------------------------------------------


def price_history(close: pd.Series) -> go.Figure:
    fig = go.Figure(
        go.Scatter(x=close.index, y=close.values, mode="lines", name="Brent close",
                   line=dict(color=TEAL, width=1.3))
    )
    return _style(fig, "Brent close price", "Date", "USD per barrel")


def returns_history(returns: pd.Series) -> go.Figure:
    fig = go.Figure(
        go.Scatter(x=returns.index, y=returns.values, mode="lines", name="daily log return",
                   line=dict(color=TEAL, width=0.7))
    )
    return _style(fig, "Daily log returns", "Date", "Return (%)")


def rolling_volatility(vol: pd.Series) -> go.Figure:
    fig = go.Figure(
        go.Scatter(x=vol.index, y=vol.values, mode="lines", name="21-day realized vol",
                   line=dict(color=SAND, width=1.4))
    )
    return _style(fig, "Rolling 21-day volatility, annualized", "Date", "Volatility (%)")


def return_distribution(returns: pd.Series) -> go.Figure:
    fig = go.Figure(
        go.Histogram(x=returns.values, nbinsx=140, marker_color=TEAL, opacity=0.8,
                     name="historical returns")
    )
    return _style(fig, "Distribution of daily returns", "Return (%)", "Days")


def acf_comparison(return_acf: np.ndarray, squared_acf: np.ndarray, n_obs: int) -> go.Figure:
    lags = np.arange(1, len(return_acf))
    band = 1.96 / np.sqrt(n_obs)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=lags, y=return_acf[1:], mode="lines+markers",
                             name="returns", line=dict(color="#7A868A", width=1.6)))
    fig.add_trace(go.Scatter(x=lags, y=squared_acf[1:], mode="lines+markers",
                             name="squared returns", line=dict(color=TEAL, width=2.4)))
    for sign in (1, -1):
        fig.add_hline(y=sign * band, line=dict(color=GRID, dash="dash", width=1))
    fig.add_hline(y=0, line=dict(color="rgba(128,138,140,0.5)", width=1))
    return _style(fig, "Autocorrelation: returns vs squared returns", "Lag (trading days)",
                  "Autocorrelation")


def hill_profile_chart(ks: np.ndarray, left: np.ndarray, right: np.ndarray) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=ks, y=left, mode="lines+markers", name="loss tail",
                             line=dict(color=TEAL, width=2)))
    fig.add_trace(go.Scatter(x=ks, y=right, mode="lines+markers", name="gain tail",
                             line=dict(color=SAND, width=2)))
    fig.add_hline(y=2, line=dict(color=BAD, dash="dash", width=1),
                  annotation_text="α=2: finite variance boundary", annotation_position="bottom right")
    fig.add_hline(y=4, line=dict(color="rgba(128,138,140,0.6)", dash="dot", width=1),
                  annotation_text="α=4: finite kurtosis boundary", annotation_position="top right")
    return _style(fig, "Hill tail-index profile", "k (largest observations used)",
                  "Tail index α")


def mean_excess_chart(thresholds: np.ndarray, excess: np.ndarray) -> go.Figure:
    fig = go.Figure(
        go.Scatter(x=thresholds, y=excess, mode="lines+markers", name="mean excess",
                   line=dict(color=TEAL, width=2))
    )
    return _style(fig, "Mean excess over threshold (losses)", "Threshold u (loss, %)",
                  "E[L − u | L > u]")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

_FAN_BANDS = ((1, 99, 0.10), (5, 95, 0.16), (25, 75, 0.24))


def fan_chart(paths_pct: np.ndarray, initial_price: float | None = None,
              as_price: bool = False) -> go.Figure:
    """Percentile fan through time. Carries the distribution without drawing every path."""
    from .risk import cumulative_log_returns, price_paths

    if as_price:
        if initial_price is None:
            raise ValueError("initial_price is required for a price fan chart.")
        series = price_paths(paths_pct, initial_price)
        x = np.arange(series.shape[1])
        y_title, title = "Price (USD)", "Scenario fan chart — price"
    else:
        series = np.expm1(cumulative_log_returns(paths_pct) / 100.0) * 100.0
        x = np.arange(1, series.shape[1] + 1)
        y_title, title = "Cumulative return (%)", "Scenario fan chart — cumulative return"

    fig = go.Figure()
    for lo, hi, alpha in _FAN_BANDS:
        low = np.percentile(series, lo, axis=0)
        high = np.percentile(series, hi, axis=0)
        fig.add_trace(go.Scatter(
            x=np.concatenate([x, x[::-1]]),
            y=np.concatenate([high, low[::-1]]),
            fill="toself", fillcolor=TEAL_FILL.format(alpha=alpha),
            line=dict(width=0), hoverinfo="skip", name=f"{lo}–{hi}%",
        ))
    fig.add_trace(go.Scatter(x=x, y=np.percentile(series, 50, axis=0), mode="lines",
                             name="median", line=dict(color=TEAL, width=2.4)))
    if as_price:
        fig.add_hline(y=initial_price, line=dict(color=SAND, dash="dash", width=1.2),
                      annotation_text="today", annotation_position="bottom right")
    else:
        fig.add_hline(y=0, line=dict(color=SAND, dash="dash", width=1.2))
    return _style(fig, title, "Trading day ahead", y_title, height=460)


def sample_paths(paths_pct: np.ndarray, n_show: int, seed: int,
                 initial_price: float | None = None, as_price: bool = False,
                 highlight: np.ndarray | None = None) -> go.Figure:
    """A readable sample of individual paths, never the whole set."""
    from .risk import cumulative_log_returns, price_paths

    total = paths_pct.shape[0]
    n_show = int(min(n_show, total))
    rng = np.random.default_rng(seed)
    idx = rng.choice(total, size=n_show, replace=False)

    if as_price:
        if initial_price is None:
            raise ValueError("initial_price is required for price paths.")
        series = price_paths(paths_pct, initial_price)
        x = np.arange(series.shape[1])
        y_title = "Price (USD)"
    else:
        series = np.expm1(cumulative_log_returns(paths_pct) / 100.0) * 100.0
        x = np.arange(1, series.shape[1] + 1)
        y_title = "Cumulative return (%)"

    fig = go.Figure()
    for i in idx:
        fig.add_trace(go.Scatter(x=x, y=series[i], mode="lines", showlegend=False,
                                 line=dict(color=TEAL_FILL.format(alpha=0.30), width=1),
                                 hovertemplate=f"scenario {i}<br>%{{y:.2f}}<extra></extra>"))
    if highlight is not None:
        for i in highlight[: min(len(highlight), 40)]:
            fig.add_trace(go.Scatter(x=x, y=series[i], mode="lines", showlegend=False,
                                     line=dict(color=BAD, width=1.4),
                                     hovertemplate=f"stressed scenario {i}<br>%{{y:.2f}}<extra></extra>"))
    return _style(fig, f"{n_show} individual scenarios out of {total:,}",
                  "Trading day ahead", y_title, height=440)


def distribution(values: np.ndarray, title: str, x_label: str,
                 reference: float | None = None, reference_label: str = "history",
                 percent: bool = False) -> go.Figure:
    v = np.asarray(values, dtype=float)
    if percent:
        v = v * 100.0
    fig = go.Figure(go.Histogram(x=v, nbinsx=70, marker_color=TEAL, opacity=0.82,
                                 name="scenarios"))
    if reference is not None:
        ref = reference * 100.0 if percent else reference
        fig.add_vline(x=ref, line=dict(color=SAND, dash="dash", width=2),
                      annotation_text=reference_label, annotation_position="top right")
    return _style(fig, title, x_label, "Scenarios")


def overlay_distribution(reference: np.ndarray, comparison: np.ndarray, title: str,
                         x_label: str, percent: bool = False,
                         reference_label: str = "historical",
                         comparison_label: str = "synthetic") -> go.Figure:
    """Two distributions on shared bins.

    The labels are parameters because this figure is used for two different
    comparisons -- historical against synthetic, and all scenarios against a
    stressed subset -- and a hardcoded legend would mislabel one of them.
    """
    h = np.asarray(reference, dtype=float)
    s = np.asarray(comparison, dtype=float)
    if percent:
        h, s = h * 100.0, s * 100.0
    lo = float(min(np.min(h), np.min(s)))
    hi = float(max(np.percentile(h, 99.5), np.percentile(s, 99.5)))
    bins = dict(start=lo, end=hi, size=(hi - lo) / 60 if hi > lo else 1.0)
    fig = go.Figure()
    fig.add_trace(go.Histogram(x=h, xbins=bins, histnorm="probability density",
                               marker_color=SAND, opacity=0.55, name=reference_label))
    fig.add_trace(go.Histogram(x=s, xbins=bins, histnorm="probability density",
                               marker_color=TEAL, opacity=0.55, name=comparison_label))
    fig.update_layout(barmode="overlay")
    return _style(fig, title, x_label, "Density")


def squared_acf_validation(real_full: np.ndarray, real_block_mean: np.ndarray,
                           real_p05: np.ndarray, real_p95: np.ndarray,
                           synthetic_mean: np.ndarray, horizon: int) -> go.Figure:
    """The estimator lesson in one picture.

    The full-sample historical curve sits far above the block curves. It is not a
    fairer target -- it is a different estimator, and comparing the synthetic
    block mean against it measures the window length, not the model.
    """
    lags = np.arange(1, len(real_full))
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=np.concatenate([lags, lags[::-1]]),
        y=np.concatenate([real_p95[1:], real_p05[1:][::-1]]),
        fill="toself", fillcolor=SAND_FILL.format(alpha=0.16), line=dict(width=0),
        hoverinfo="skip", name=f"historical 5–95% across {horizon}d blocks",
    ))
    fig.add_trace(go.Scatter(x=lags, y=real_full[1:], mode="lines+markers",
                             name="historical, full sample (different estimator)",
                             line=dict(color="#7A868A", width=1.4, dash="dot")))
    fig.add_trace(go.Scatter(x=lags, y=real_block_mean[1:], mode="lines+markers",
                             name=f"historical, mean over {horizon}d blocks",
                             line=dict(color=SAND, width=2.2)))
    fig.add_trace(go.Scatter(x=lags, y=synthetic_mean[1:], mode="lines+markers",
                             name=f"synthetic, mean over {horizon}d paths",
                             line=dict(color=TEAL, width=2.4)))
    fig.add_hline(y=0, line=dict(color=GRID, width=1))
    return _style(fig, "Squared-return ACF, horizon-matched", "Lag (trading days)",
                  "Autocorrelation", height=460)


def scenario_detail(returns: np.ndarray, volatility: np.ndarray, initial_price: float,
                    scenario_id: int) -> go.Figure:
    """Four stacked views of a single scenario."""
    from plotly.subplots import make_subplots

    from .risk import price_paths

    single = returns.reshape(1, -1)
    prices = price_paths(single, initial_price)[0]
    cumulative = np.expm1(np.cumsum(returns) / 100.0) * 100.0
    running_max = np.maximum.accumulate(prices)
    drawdown = (1.0 - prices / running_max) * 100.0
    days = np.arange(1, returns.size + 1)

    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.05,
        subplot_titles=("Price path", "Daily returns", "Conditional volatility",
                        "Drawdown from running peak"),
    )
    fig.add_trace(go.Scatter(x=np.arange(prices.size), y=prices, mode="lines",
                             line=dict(color=TEAL, width=2), name="price"), row=1, col=1)
    fig.add_hline(y=initial_price, line=dict(color=SAND, dash="dash", width=1), row=1, col=1)
    fig.add_trace(go.Bar(x=days, y=returns, marker_color=TEAL, name="return"), row=2, col=1)
    fig.add_trace(go.Scatter(x=days, y=volatility, mode="lines",
                             line=dict(color=SAND, width=2), name="σ"), row=3, col=1)
    fig.add_trace(go.Scatter(x=np.arange(prices.size), y=drawdown, mode="lines",
                             fill="tozeroy", fillcolor="rgba(172,58,46,0.18)",
                             line=dict(color=BAD, width=1.6), name="drawdown"), row=4, col=1)

    fig.update_layout(template="plotly_white", height=760, showlegend=False,
                      margin=dict(l=60, r=24, t=64, b=48),
                      title=dict(text=f"Scenario {scenario_id} — terminal return "
                                      f"{cumulative[-1]:+.1f}%", x=0, font=dict(size=15)),
                      font=dict(family="Source Sans 3, system-ui, sans-serif", size=13))
    fig.update_yaxes(title_text="USD", row=1, col=1)
    fig.update_yaxes(title_text="%", row=2, col=1)
    fig.update_yaxes(title_text="%/day", row=3, col=1)
    fig.update_yaxes(title_text="%", row=4, col=1)
    fig.update_xaxes(title_text="Trading day", row=4, col=1)
    return fig


def stress_volatility(experiment, days_after: int) -> go.Figure:
    """Imposed shock days, then the expected decay. Colour separates the two."""
    n_shock = experiment.shocks_pct.size
    shock_days = np.arange(1, n_shock + 1)
    after_days = np.arange(n_shock + 1, n_shock + 1 + days_after)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=shock_days, y=experiment.conditional_volatility,
                             mode="lines+markers", name="during imposed shock",
                             line=dict(color=BAD, width=2.6)))
    if days_after:
        fig.add_trace(go.Scatter(x=after_days, y=experiment.volatility_after,
                                 mode="lines", name="expected decay afterwards",
                                 line=dict(color=TEAL, width=2.2, dash="dot")))
    fig.add_hline(y=experiment.starting_volatility, line=dict(color=SAND, dash="dash", width=1.4),
                  annotation_text="volatility before the shock", annotation_position="bottom right")
    return _style(fig, "Conditional volatility through an assumed shock",
                  "Trading day", "Volatility (%/day)", height=420)


def convergence_chart(points, metric: str, label: str) -> go.Figure:
    counts = [p.n_paths for p in points]
    values = [getattr(p, metric) for p in points]
    fig = go.Figure(go.Scatter(x=counts, y=values, mode="lines+markers",
                               line=dict(color=TEAL, width=2.4), name=label))
    fig.update_xaxes(type="log")
    return _style(fig, f"{label} as the number of paths grows", "Paths (log scale)",
                  label, height=340)
