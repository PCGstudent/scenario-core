"""UI-independent services. Core imports are the only link to the original project."""
from __future__ import annotations

from copy import copy
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from time import perf_counter
import warnings

import numpy as np
from scipy import stats

from xtra_takehome.challenger import GjrSkewTGenerator
from xtra_takehome.config import Config
from xtra_takehome.data import fetch_close, log_returns_pct
from xtra_takehome.diagnostics import acf_values, hill_profile, mean_excess, summarize
from xtra_takehome import validation as val
from xtra_takehome.windows import compute_window_stats, non_overlapping_blocks, rolling_blocks
from .risk import metrics, path_table

ROOT = Path(__file__).resolve().parents[2]
CFG = Config()
MODEL_NAME = "GJR-GARCH(1,1,1) · Hansen skewed-t"


def core_fingerprint():
    return sha256(b"".join(p.read_bytes() for p in sorted((ROOT / "src/xtra_takehome").glob("*.py")))).hexdigest()


def cache_fingerprint():
    """Invalidate cached computations when either core or application code changes."""
    local = sorted((ROOT / "brent_risk_lab/lab").glob("*.py"))
    return sha256(core_fingerprint().encode() + b"".join(p.read_bytes() for p in local)).hexdigest()


def load_data():
    # Existing cache is read if present. A fresh download goes to the isolated lab.
    existing = ROOT / ".cache"
    cache = existing if (existing / f"BZF_{CFG.start}_{CFG.end}.csv").exists() else ROOT / "brent_risk_lab/.cache"
    close = fetch_close(CFG.ticker, CFG.start, CFG.end, cache_dir=cache)
    return close, log_returns_pct(close)


def fit(returns):
    from arch.utility.exceptions import ConvergenceWarning
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        return GjrSkewTGenerator().fit(returns)


def diagnostics(returns):
    r, sq = acf_values(returns, CFG.max_acf_lag)
    return dict(summary=asdict(summarize(returns)), acf=r, squared_acf=sq,
                hill=hill_profile(returns), excess=mean_excess(returns),
                rolling_vol=returns.rolling(21).std() * np.sqrt(252))


def fitted_states(generator):
    """Compatibility boundary with the original fitted-state simulator.

    These arrays already exist in the submitted core, even without the optional
    UI extensions another application is developing. No fitted object is mutated.
    """
    residuals, variances = generator._residuals, generator._variances
    if residuals is None or variances is None or len(residuals) == 0:
        raise ValueError("Fit the generator before requesting scenarios.")
    return residuals, variances


def variance_step(p, residual, variance):
    """Exact core recursion, used only for tracing and deterministic experiments."""
    return np.maximum(p.omega + p.alpha * residual**2 + p.gamma * (residual < 0) * residual**2
                      + p.beta * variance, 1e-12)


@dataclass
class Run:
    returns: np.ndarray
    variances: np.ndarray
    settings: dict
    elapsed: float
    summary: object


def generate(generator, n_paths, horizon, seed, p0, as_of, mode="latest", explicit=None):
    if not 1 <= n_paths <= 10000 or not 2 <= horizon <= 252 or seed < 0:
        raise ValueError("Use 1–10,000 paths, 2–252 trading days and a nonnegative seed.")
    residuals, variances = fitted_states(generator)
    g = copy(generator)
    if mode == "latest":
        residuals, variances = residuals[-1:], variances[-1:]
    elif mode == "explicit":
        if explicit is None or not np.isfinite(explicit).all() or explicit[1] <= 0:
            raise ValueError("Explicit (residual, variance) must be finite with positive variance.")
        residuals, variances = np.array([explicit[0]]), np.array([explicit[1]])
    elif mode != "historical_mix":
        raise ValueError("Unknown starting-state mode.")
    g._residuals, g._variances = residuals, variances
    started = perf_counter()
    # All innovation draws and returns come from the submitted implementation.
    paths = g.simulate(n_steps=horizon, n_paths=n_paths, seed=seed)
    state_seed, _ = np.random.SeedSequence(seed).spawn(2)
    indices = np.random.default_rng(state_seed).integers(0, len(residuals), size=n_paths)
    eps, v = residuals[indices], variances[indices]
    trace = np.empty_like(paths)
    for t in range(horizon):
        v = variance_step(g.params_, eps, v)
        trace[:, t] = v
        eps = paths[:, t] - g.params_.mu
    settings = dict(n_paths=n_paths, horizon=horizon, seed=int(seed), initial_state=mode,
                    explicit_state=list(explicit) if explicit is not None else None,
                    initial_price=float(p0), as_of=str(as_of), model=MODEL_NAME,
                    parameters=asdict(g.params_), core_sha256=core_fingerprint(),
                    rng="SeedSequence(seed).spawn(2): state and innovation streams")
    return Run(paths, trace, settings, perf_counter()-started, path_table(paths, p0))


def validate(generator, returns):
    """Canonical gates are executed directly; lab controls never reach them."""
    x = returns.to_numpy()
    paths = generator.simulate(CFG.horizon, CFG.n_paths, CFG.seed)
    pooled, reported, extras = val.validate(returns, paths, CFG.horizon, CFG.max_acf_lag)
    real = compute_window_stats(rolling_blocks(x, CFG.horizon))
    syn = compute_window_stats(paths)
    disjoint = non_overlapping_blocks(x, CFG.horizon)
    disjoint_stats = compute_window_stats(disjoint)
    matched = val.validate_horizon_matched(real, syn, val.pooled_context(x).mean_standard_error)
    # AGENTS invariant 20: unstable pooled moments are quoted across seeds.
    moment_runs = []
    for seed in range(40, 50):
        sample = generator.simulate(CFG.horizon, CFG.n_paths, seed).reshape(-1)
        moment_runs.append([stats.skew(sample, bias=False), stats.kurtosis(sample, fisher=True, bias=False)])
    moments = np.asarray(moment_runs)
    return dict(pooled=pooled, reported=reported, matched=matched, extras=extras, real=real, synthetic=syn,
                paths=paths, extremes=val.extreme_region_checks(disjoint_stats, syn),
                references=val.matched_sample_reference(generator, x),
                acf_floor=val.acf_monte_carlo_floor(generator, CFG.horizon, CFG.n_paths, CFG.max_acf_lag),
                leave_out=val.leave_out_sensitivity("synthetic", paths, syn.volatility)
                  + val.leave_out_sensitivity("historical", disjoint, disjoint_stats.volatility),
                beyond_max_fraction=val.beyond_historical_max_fraction(real, syn),
                pooled_moment_medians=np.median(moments, axis=0),
                pooled_moment_ranges=np.stack([moments.min(axis=0), moments.max(axis=0)]),
                config=asdict(CFG))


def convergence(generator, run, counts=(100, 500, 1000, 5000, 10000), replications=3):
    """Independent replications; nested prefixes within each replication.

    A prefix is a subset of whole paths, never a stitched time series.
    """
    import pandas as pd
    children = np.random.SeedSequence(run.settings["seed"]).spawn(replications)
    rows = []
    for rep, child in enumerate(children):
        seed = int(child.generate_state(1)[0])
        largest = generate(generator, max(counts), run.settings["horizon"], seed,
                           run.settings["initial_price"], run.settings["as_of"],
                           run.settings["initial_state"], run.settings["explicit_state"])
        for n in counts:
            m = metrics(largest.returns[:n], run.settings["initial_price"]).set_index("metric")
            rows.append(dict(replication=rep, paths=n,
                             VaR95=m.loc["Terminal horizon: VaR95", "value"],
                             VaR99=m.loc["Terminal horizon: VaR99", "value"],
                             ES95=m.loc["Terminal horizon: ES95", "value"],
                             Drawdown_p95=m.loc["Maximum drawdown p95", "value"]))
    return pd.DataFrame(rows)
