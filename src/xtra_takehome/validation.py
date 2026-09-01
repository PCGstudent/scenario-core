from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.tsa.stattools import acf

from .metrics import (
    historical_rolling_drawdowns,
    mean_squared_return_acf,
    path_max_drawdowns,
    var_es,
)


@dataclass(frozen=True)
class Gate:
    metric: str
    real: float
    synthetic: float
    error: float
    threshold: float
    error_type: str
    passed: bool


def _relative_error(real: float, synthetic: float, floor: float = 1e-8) -> float:
    return abs(synthetic - real) / max(abs(real), floor)


def _gate_rel(metric: str, real: float, synthetic: float, threshold: float) -> Gate:
    err = _relative_error(real, synthetic)
    return Gate(metric, real, synthetic, err, threshold, "relative", err <= threshold)


def _gate_abs(metric: str, real: float, synthetic: float, threshold: float) -> Gate:
    err = abs(synthetic - real)
    return Gate(metric, real, synthetic, err, threshold, "absolute", err <= threshold)


def validate(
    real_returns: pd.Series,
    synthetic_paths: np.ndarray,
    horizon: int = 252,
    acf_lags: int = 20,
) -> tuple[list[Gate], dict[str, np.ndarray | float]]:
    real = np.asarray(real_returns, dtype=float)
    syn_paths = np.asarray(synthetic_paths, dtype=float)
    syn = syn_paths.reshape(-1)

    gates: list[Gate] = []

    # Marginal moments
    gates.append(
        _gate_abs("mean return (pp)", float(np.mean(real)), float(np.mean(syn)), 0.10)
    )
    gates.append(
        _gate_rel(
            "volatility",
            float(np.std(real, ddof=1)),
            float(np.std(syn, ddof=1)),
            0.10,
        )
    )
    gates.append(
        _gate_abs(
            "skewness",
            float(stats.skew(real, bias=False)),
            float(stats.skew(syn, bias=False)),
            0.50,
        )
    )
    gates.append(
        _gate_abs(
            "excess kurtosis",
            float(stats.kurtosis(real, fisher=True, bias=False)),
            float(stats.kurtosis(syn, fisher=True, bias=False)),
            2.00,
        )
    )

    # Tail quantiles
    quantile_specs = [
        ("q01", 0.01, 0.20),
        ("q05", 0.05, 0.15),
        ("q95", 0.95, 0.15),
        ("q99", 0.99, 0.20),
    ]
    for name, q, threshold in quantile_specs:
        gates.append(
            _gate_rel(
                name,
                float(np.quantile(real, q)),
                float(np.quantile(syn, q)),
                threshold,
            )
        )

    # Risk measures: positive loss magnitudes.
    for level, var_thr, es_thr in [(0.95, 0.15, 0.20), (0.99, 0.20, 0.25)]:
        real_var, real_es = var_es(real, level)
        syn_var, syn_es = var_es(syn, level)
        gates.append(_gate_rel(f"VaR {int(level*100)}%", real_var, syn_var, var_thr))
        gates.append(_gate_rel(f"ES {int(level*100)}%", real_es, syn_es, es_thr))

    # Volatility clustering
    real_sq_acf = acf(real**2, nlags=acf_lags, fft=True)
    syn_sq_acf = mean_squared_return_acf(syn_paths, nlags=acf_lags)
    acf_mae = float(np.mean(np.abs(real_sq_acf[1:] - syn_sq_acf[1:])))
    gates.append(
        Gate(
            metric=f"squared-return ACF MAE (lags 1-{acf_lags})",
            real=0.0,
            synthetic=acf_mae,
            error=acf_mae,
            threshold=0.05,
            error_type="absolute",
            passed=acf_mae <= 0.05,
        )
    )

    # Like-for-like 252-day maximum drawdowns.
    hist_dd = historical_rolling_drawdowns(real, horizon=horizon)
    syn_dd = path_max_drawdowns(syn_paths)
    for label, q, threshold in [
        ("drawdown median", 0.50, 0.25),
        ("drawdown p95", 0.95, 0.30),
    ]:
        gates.append(
            _gate_rel(
                label,
                float(np.quantile(hist_dd, q)),
                float(np.quantile(syn_dd, q)),
                threshold,
            )
        )

    extras: dict[str, np.ndarray | float] = {
        "real_sq_acf": real_sq_acf,
        "synthetic_sq_acf": syn_sq_acf,
        "historical_drawdowns": hist_dd,
        "synthetic_drawdowns": syn_dd,
    }
    return gates, extras
