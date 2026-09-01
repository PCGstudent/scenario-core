from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class GarchTParams:
    mu: float
    omega: float
    alpha: float
    beta: float
    nu: float
    initial_variance: float

    @property
    def persistence(self) -> float:
        return self.alpha + self.beta

    @property
    def implied_unconditional_variance(self) -> float:
        """omega / (1 - persistence), the level the recursion drifts toward."""
        slack = 1.0 - self.persistence
        if slack <= 0.0:
            return float("inf")
        return float(self.omega / slack)


def _standardized_t_draws(
    rng: np.random.Generator,
    nu: float,
    shape: tuple[int, int],
) -> np.ndarray:
    if nu <= 2:
        raise ValueError("Student-t degrees of freedom must exceed 2 for finite variance.")
    z = rng.standard_t(df=nu, size=shape)
    return z * np.sqrt((nu - 2.0) / nu)


def simulate_garch_t(
    params: GarchTParams,
    horizon: int,
    n_paths: int,
    seed: int,
) -> np.ndarray:
    """Standalone GARCH-t simulation from a single documented initial variance.

    This helper is retained for simple deterministic unit tests. The fitted generator
    below uses empirical fitted-state initialization so model comparisons do not mix
    a fixed unconditional start for one model with historical-state starts for another.
    """
    if horizon <= 0 or n_paths <= 0:
        raise ValueError("horizon and n_paths must be positive.")

    rng = np.random.default_rng(seed)
    z = _standardized_t_draws(rng, params.nu, (n_paths, horizon))

    returns = np.empty((n_paths, horizon), dtype=float)
    variance = np.empty((n_paths, horizon), dtype=float)

    variance[:, 0] = max(params.initial_variance, 1e-12)
    eps_prev = np.zeros(n_paths, dtype=float)

    for t in range(horizon):
        if t > 0:
            variance[:, t] = (
                params.omega
                + params.alpha * eps_prev**2
                + params.beta * variance[:, t - 1]
            )
            variance[:, t] = np.maximum(variance[:, t], 1e-12)

        eps = np.sqrt(variance[:, t]) * z[:, t]
        returns[:, t] = params.mu + eps
        eps_prev = eps

    return returns


def simulate_garch_t_from_states(
    params: GarchTParams,
    residuals: np.ndarray,
    variances: np.ndarray,
    horizon: int,
    n_paths: int,
    seed: int,
) -> np.ndarray:
    """GARCH-t simulation initialized from sampled historical fitted states."""
    if horizon <= 0 or n_paths <= 0:
        raise ValueError("horizon and n_paths must be positive.")
    if residuals.size == 0 or variances.size == 0 or residuals.size != variances.size:
        raise ValueError("residuals and variances must be non-empty aligned arrays.")

    # Independent child streams for state sampling and innovations, so that
    # consecutive seeds in the robustness study do not share a generator stream.
    state_seed, innovation_seed = np.random.SeedSequence(seed).spawn(2)

    state_rng = np.random.default_rng(state_seed)
    state_idx = state_rng.integers(0, residuals.size, size=n_paths)
    eps_prev = np.asarray(residuals[state_idx], dtype=float).copy()
    var_prev = np.asarray(variances[state_idx], dtype=float).copy()
    z = _standardized_t_draws(np.random.default_rng(innovation_seed), params.nu, (n_paths, horizon))

    out = np.empty((n_paths, horizon), dtype=float)
    for t in range(horizon):
        variance = (
            params.omega
            + params.alpha * eps_prev**2
            + params.beta * var_prev
        )
        variance = np.maximum(variance, 1e-12)
        eps = np.sqrt(variance) * z[:, t]
        out[:, t] = params.mu + eps
        eps_prev = eps
        var_prev = variance

    return out


class GarchTGenerator:
    """Documented fit-then-simulate interface for the assessment."""

    def __init__(self) -> None:
        self.params_: GarchTParams | None = None
        self.fit_summary_: str | None = None
        self._residuals: np.ndarray | None = None
        self._variances: np.ndarray | None = None

    def fit(self, returns: pd.Series) -> "GarchTGenerator":
        from arch import arch_model

        x = pd.Series(returns, dtype=float).dropna()

        model = arch_model(
            x,
            mean="Constant",
            vol="GARCH",
            p=1,
            o=0,
            q=1,
            dist="StudentsT",
            rescale=False,
        )
        result = model.fit(disp="off")

        p = result.params
        mu = float(p.get("mu", p.get("Const", 0.0)))
        omega = float(p["omega"])
        alpha = float(p["alpha[1]"])
        beta = float(p["beta[1]"])
        nu = float(p["nu"])

        persistence = alpha + beta
        if persistence < 0.999999:
            unconditional_variance = omega / max(1.0 - persistence, 1e-12)
        else:
            unconditional_variance = float(np.var(x, ddof=1))

        self.params_ = GarchTParams(
            mu=mu,
            omega=omega,
            alpha=alpha,
            beta=beta,
            nu=nu,
            initial_variance=max(unconditional_variance, 1e-12),
        )
        self.fit_summary_ = str(result.summary())

        residuals = np.asarray(result.resid, dtype=float)
        variances = np.asarray(result.conditional_volatility, dtype=float) ** 2
        valid = np.isfinite(residuals) & np.isfinite(variances) & (variances > 0)
        self._residuals = residuals[valid]
        self._variances = variances[valid]
        if self._residuals.size == 0:
            raise RuntimeError("No valid fitted states were produced by the GARCH model.")
        return self

    def simulate(self, n_steps: int, n_paths: int, seed: int) -> np.ndarray:
        if self.params_ is None or self._residuals is None or self._variances is None:
            raise RuntimeError("Call fit() before simulate().")
        return simulate_garch_t_from_states(
            self.params_,
            residuals=self._residuals,
            variances=self._variances,
            horizon=n_steps,
            n_paths=n_paths,
            seed=seed,
        )
