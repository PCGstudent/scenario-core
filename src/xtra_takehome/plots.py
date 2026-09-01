from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

# Figures are written to disk, never displayed. Selecting the non-interactive
# backend before pyplot is imported keeps the run working on headless machines
# and in CI, where importing a GUI backend fails outright.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

from .diagnostics import acf_values, hill_profile, mean_excess, student_t_qq
from .windows import WindowStats


def _save(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def plot_diagnostics_acf(returns: pd.Series, path: Path, nlags: int = 20) -> None:
    r_acf, sq_acf = acf_values(returns, nlags=nlags)
    # Lag 0 is identically 1 and visually compresses the economically relevant lags.
    lags = np.arange(1, nlags + 1)
    approx_band = 1.96 / np.sqrt(len(returns))

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(lags, r_acf[1:], marker="o", label="returns")
    ax.plot(lags, sq_acf[1:], marker="o", label="squared returns")
    ax.axhline(0.0, linewidth=1)
    ax.axhline(approx_band, linewidth=0.8, linestyle="--", label="approx. 95% white-noise band")
    ax.axhline(-approx_band, linewidth=0.8, linestyle="--")
    ax.set_title("ACF: returns vs squared returns (lags 1–20)")
    ax.set_xlabel("Lag")
    ax.set_ylabel("Autocorrelation")
    ax.legend()
    _save(fig, path)


def plot_student_t_qq(returns: pd.Series, path: Path) -> None:
    theoretical, observed, params = student_t_qq(returns)
    lo = min(theoretical.min(), observed.min())
    hi = max(theoretical.max(), observed.max())

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.scatter(theoretical, observed, s=10, alpha=0.65)
    ax.plot([lo, hi], [lo, hi], linewidth=1)
    ax.set_title(f"QQ vs fitted Student-t (df={params[0]:.2f})")
    ax.set_xlabel("Theoretical quantiles")
    ax.set_ylabel("Observed return quantiles")
    _save(fig, path)


def plot_marginal_comparison(
    real_returns: pd.Series,
    synthetic_paths: np.ndarray,
    path: Path,
) -> None:
    real = np.asarray(real_returns, dtype=float)
    syn = np.asarray(synthetic_paths, dtype=float).reshape(-1)

    lo = min(np.quantile(real, 0.002), np.quantile(syn, 0.002))
    hi = max(np.quantile(real, 0.998), np.quantile(syn, 0.998))
    bins = np.linspace(lo, hi, 80)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(real, bins=bins, density=True, alpha=0.55, label="real")
    ax.hist(syn, bins=bins, density=True, alpha=0.45, label="synthetic")
    ax.set_title("Marginal return distribution")
    ax.set_xlabel("Daily log return (%)")
    ax.set_ylabel("Density")
    ax.legend()
    _save(fig, path)


def plot_squared_acf_comparison(
    real_full_sample_acf: np.ndarray,
    real_stats: WindowStats,
    synthetic_stats: WindowStats,
    path: Path,
) -> None:
    """Squared-return ACF, estimated like-for-like.

    The full-sample historical ACF is shown for context only. It is estimated on
    ~4,158 observations while every synthetic curve is estimated on blocks of
    `horizon` observations, where the sample ACF is strongly biased toward zero.
    The honest comparison is the historical block mean against the synthetic block
    mean, inside the historical block-to-block spread.
    """
    # Exclude lag 0 (=1 by definition) so differences across lags 1..L are visible.
    lags = np.arange(1, len(real_full_sample_acf))
    fig, ax = plt.subplots(figsize=(8.5, 4.8))

    ax.fill_between(
        lags,
        real_stats.squared_acf_p05[1:],
        real_stats.squared_acf_p95[1:],
        alpha=0.20,
        label=f"real, 5-95% across {real_stats.horizon}d windows",
    )
    ax.plot(
        lags,
        real_full_sample_acf[1:],
        marker="o",
        markersize=4,
        linewidth=1,
        linestyle=":",
        label="real, full sample (different estimator)",
    )
    ax.plot(
        lags,
        real_stats.mean_squared_acf[1:],
        marker="o",
        markersize=4,
        label=f"real, mean over {real_stats.horizon}d windows",
    )
    ax.plot(
        lags,
        synthetic_stats.mean_squared_acf[1:],
        marker="s",
        markersize=4,
        label=f"synthetic, mean over {synthetic_stats.horizon}d paths",
    )
    ax.axhline(0.0, linewidth=1, color="black")
    ax.set_title("Squared-return ACF, horizon-matched (lags 1-20)")
    ax.set_xlabel("Lag")
    ax.set_ylabel("Autocorrelation")
    ax.legend(fontsize=8)
    _save(fig, path)


def plot_drawdown_comparison(
    historical_drawdowns: np.ndarray,
    synthetic_drawdowns: np.ndarray,
    path: Path,
) -> None:
    bins = np.linspace(
        0.0,
        max(float(np.max(historical_drawdowns)), float(np.max(synthetic_drawdowns))),
        60,
    )
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(
        historical_drawdowns,
        bins=bins,
        density=True,
        alpha=0.55,
        label="historical rolling 252d",
    )
    ax.hist(
        synthetic_drawdowns,
        bins=bins,
        density=True,
        alpha=0.45,
        label="synthetic 252d",
    )
    ax.set_title("252-day maximum drawdown distribution")
    ax.set_xlabel("Maximum drawdown")
    ax.set_ylabel("Density")
    ax.legend()
    _save(fig, path)


def plot_tail_diagnostics(returns: pd.Series, path: Path) -> None:
    """Hill tail-index profile and mean-excess plot for the loss tail."""
    ks, left, right = hill_profile(returns)
    thresholds, excess = mean_excess(returns)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5))

    ax1.plot(ks, left, marker="o", label="left tail (losses)")
    ax1.plot(ks, right, marker="s", label="right tail (gains)")
    ax1.axhline(2.0, linestyle="--", linewidth=0.9, color="crimson")
    ax1.axhline(4.0, linestyle=":", linewidth=0.9, color="grey")
    ax1.annotate(
        "alpha=2: variance boundary",
        xy=(ks[-1], 2.0),
        xytext=(0, 4),
        textcoords="offset points",
        ha="right",
        fontsize=8,
        color="crimson",
    )
    ax1.annotate(
        "alpha=4: kurtosis boundary",
        xy=(ks[-1], 4.0),
        xytext=(0, 4),
        textcoords="offset points",
        ha="right",
        fontsize=8,
        color="grey",
    )
    ax1.set_title("Hill tail-index profile")
    ax1.set_xlabel("k (number of order statistics)")
    ax1.set_ylabel("Estimated tail index alpha")
    ax1.legend(fontsize=8)

    ax2.plot(thresholds, excess, marker="o", markersize=4)
    ax2.set_title("Mean excess over threshold (losses)")
    ax2.set_xlabel("Threshold u (loss, %)")
    ax2.set_ylabel("E[L - u | L > u]")

    _save(fig, path)


def plot_year_severity(
    real_stats: WindowStats,
    synthetic_stats: WindowStats,
    path: Path,
) -> None:
    """Distribution of year-level statistics: historical windows vs synthetic paths.

    This is the figure the pooled comparison cannot produce. Each observation is one
    252-day block, so both sides use the same estimator on the same sample size.
    """
    panels = [
        ("volatility", "Annual-window volatility (%)", None),
        ("es99", "Expected shortfall 99% within the year", None),
        ("excess_kurtosis", "Excess kurtosis within the year", (-2, 20)),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))

    for ax, (attribute, title, clip) in zip(axes, panels):
        r = real_stats.get(attribute)
        s = synthetic_stats.get(attribute)
        lo = min(float(np.min(r)), float(np.min(s)))
        hi = max(float(np.quantile(r, 0.995)), float(np.quantile(s, 0.995)))
        if clip is not None:
            lo, hi = max(lo, clip[0]), min(hi, clip[1])
        bins = np.linspace(lo, hi, 50)

        ax.hist(r, bins=bins, density=True, alpha=0.55, label="historical windows")
        ax.hist(s, bins=bins, density=True, alpha=0.45, label="synthetic paths")

        worst = float(np.max(r))
        if lo <= worst <= hi:
            ax.axvline(
                worst,
                color="crimson",
                linestyle="--",
                linewidth=1.1,
                label="worst observed year",
            )
        else:
            # Keep the histogram readable rather than letting one reference line
            # stretch the axis; state the off-scale value instead.
            ax.annotate(
                f"worst observed year: {worst:.1f} (off scale)",
                xy=(0.97, 0.72),
                xycoords="axes fraction",
                ha="right",
                fontsize=7,
                color="crimson",
            )
        # The reference line must not be allowed to rescale the axis.
        ax.set_xlim(lo, hi)
        ax.set_title(title, fontsize=10)
        ax.set_ylabel("Density")
        ax.legend(fontsize=7)

    fig.suptitle(
        f"Year-level severity, {real_stats.horizon}-day blocks on both sides",
        fontsize=11,
    )
    _save(fig, path)
