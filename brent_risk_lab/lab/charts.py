"""Plotly views; chart transformations never change the underlying risk samples."""
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from .risk import prices, cumulative_return, drawdown_paths

TEAL, GOLD, RED, NAVY = "#087F8C", "#BF8B30", "#B84848", "#233E56"


def style(fig, title, xlabel, ylabel, height=380):
    fig.update_layout(template="plotly_white", title=dict(text=title, font=dict(size=16)),
                      height=height, margin=dict(l=45, r=25, t=70, b=45),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="Arial, sans-serif", color=NAVY, size=12),
                      legend=dict(orientation="h", y=-.22), hovermode="x unified")
    fig.update_xaxes(title=xlabel, gridcolor="#E1E8ED")
    fig.update_yaxes(title=ylabel, gridcolor="#E1E8ED")
    return fig


def lines(x, series, title, xlabel, ylabel, height=380):
    fig = go.Figure()
    for i, (name, values) in enumerate(series.items()):
        fig.add_trace(go.Scatter(x=x, y=values, name=name, mode="lines",
                                line=dict(color=[TEAL, GOLD, RED, NAVY][i % 4], width=1.7)))
    return style(fig, title, xlabel, ylabel, height)


def histogram(series, title, xlabel, log_y=False):
    """Common bins, all data included; optional log count axis reveals rare values."""
    fig = go.Figure()
    arrays = [np.asarray(v, dtype=float) for v in series.values()]
    low, high = min(a.min() for a in arrays), max(a.max() for a in arrays)
    bins = dict(start=low, end=high+max((high-low)*1e-8, 1e-8), size=max((high-low)/85, 1e-8))
    for i, ((name, _), values) in enumerate(zip(series.items(), arrays)):
        fig.add_trace(go.Histogram(x=values, xbins=bins, histnorm="probability",
                                  name=name, opacity=.68, marker_color=[TEAL, GOLD][i % 2]))
    fig.update_layout(barmode="overlay")
    style(fig, title, xlabel, "Probability per bin")
    if log_y:
        fig.update_yaxes(type="log", title="Probability per bin (log scale)")
    return fig


def fan(paths, p0, title="Conditional price scenarios", price=True):
    values = prices(paths, p0) if price else cumulative_return(paths)
    days = np.arange(values.shape[1])
    fig = go.Figure()
    for lo, hi, alpha in ((1, 99, .10), (5, 95, .16), (25, 75, .25)):
        low, high = np.percentile(values, [lo, hi], axis=0)
        fig.add_trace(go.Scatter(x=days, y=low, line=dict(width=0), showlegend=False,
                                hovertemplate=f"p{lo}: %{{y:.2f}}<extra></extra>"))
        fig.add_trace(go.Scatter(x=days, y=high, line=dict(width=0), fill="tonexty",
                                fillcolor=f"rgba(8,127,140,{alpha})", name=f"p{lo}–p{hi}",
                                hovertemplate=f"p{hi}: %{{y:.2f}}<extra></extra>"))
    fig.add_trace(go.Scatter(x=days, y=np.median(values, axis=0), name="Median",
                            line=dict(color=TEAL, width=2.5)))
    return style(fig, title, "Trading days after the data cut-off", "USD / barrel" if price else "Simple return (%)", 460)


def sampled(paths, p0, ids=None, color=TEAL):
    # Deterministic display selection, without a second random stream.
    ids = np.linspace(0, len(paths)-1, min(25, len(paths)), dtype=int) if ids is None else np.asarray(ids)[:40]
    values = prices(paths[ids], p0)
    fig = go.Figure()
    for i, row in zip(ids, values):
        fig.add_trace(go.Scatter(x=np.arange(len(row)), y=row, name=f"Scenario {i}",
                                showlegend=False, opacity=.65, line=dict(color=color, width=1.1)))
    return style(fig, f"{len(ids)} displayed paths · inspect IDs below", "Trading day", "USD / barrel", 420)


def detail(run, scenario_id):
    x = run.returns[scenario_id:scenario_id+1]
    values = [prices(x, run.settings["initial_price"])[0], cumulative_return(x)[0], x[0],
              np.sqrt(run.variances[scenario_id]), drawdown_paths(x)[0]]
    titles = ["Reconstructed price (USD/bbl)", "Cumulative simple return (%)", "Daily log return (%)",
              "Conditional volatility (%/day)", "Drawdown (%)"]
    fig = make_subplots(rows=5, cols=1, shared_xaxes=True, subplot_titles=titles, vertical_spacing=.06)
    for i, (title, y) in enumerate(zip(titles, values), 1):
        start = 1 if i in (3, 4) else 0
        fig.add_trace(go.Scatter(x=np.arange(start, len(y)+start), y=y, name=title,
                                line=dict(color=RED if i == 5 else TEAL, width=1.6)), row=i, col=1)
    fig.update_layout(template="plotly_white", height=1000, showlegend=False, margin=dict(t=45, b=40))
    fig.update_xaxes(title="Trading day", row=5, col=1)
    return fig


def acf_validation(v, full):
    r, s = v["real"], v["synthetic"]
    return lines(np.arange(1, len(full)), {
        "Historical 252-day block mean": r.mean_squared_acf[1:],
        "Synthetic 252-day path mean": s.mean_squared_acf[1:],
        "Historical full record (different estimator)": full[1:],
    }, "Squared-return autocorrelation", "Lag (trading days)", "Autocorrelation", 430)
