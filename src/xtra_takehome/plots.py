from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .diagnostics import acf_values, student_t_qq


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
    real_sq_acf: np.ndarray,
    synthetic_sq_acf: np.ndarray,
    path: Path,
) -> None:
    # Exclude lag 0 (=1 by definition) so differences across lags 1..L are visible.
    lags = np.arange(1, len(real_sq_acf))
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(lags, real_sq_acf[1:], marker="o", label="real")
    ax.plot(lags, synthetic_sq_acf[1:], marker="o", label="synthetic mean")
    ax.axhline(0.0, linewidth=1)
    ax.set_title("Squared-return ACF (lags 1–20)")
    ax.set_xlabel("Lag")
    ax.set_ylabel("Autocorrelation")
    ax.legend()
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
