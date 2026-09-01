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


def simulate_garch_t(
    params: GarchTParams,
    horizon: int,
    n_paths: int,
    seed: int,
) -> np.ndarray:
    """Simulate percentage returns from a fitted GARCH(1,1)-Student-t model.

    Student-t innovations are standardized to unit variance.
    """
    if params.nu <= 2:
        raise ValueError("Student-t degrees of freedom must exceed 2 for finite variance.")
    if horizon <= 0 or n_paths <= 0:
        raise ValueError("horizon and n_paths must be positive.")

    rng = np.random.default_rng(seed)
    z = rng.standard_t(df=params.nu, size=(n_paths, horizon))
    z *= np.sqrt((params.nu - 2.0) / params.nu)

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


class GarchTGenerator:
    """Documented fit-then-simulate interface for the assessment."""

    def __init__(self) -> None:
        self.params_: GarchTParams | None = None
        self.fit_summary_: str | None = None

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

        # Start simulation from a stable, data-scale variance.
        initial_variance = max(unconditional_variance, float(np.var(x, ddof=1)) * 0.25)

        self.params_ = GarchTParams(
            mu=mu,
            omega=omega,
            alpha=alpha,
            beta=beta,
            nu=nu,
            initial_variance=initial_variance,
        )
        self.fit_summary_ = str(result.summary())
        return self

    def simulate(self, n_steps: int, n_paths: int, seed: int) -> np.ndarray:
        if self.params_ is None:
            raise RuntimeError("Call fit() before simulate().")
        return simulate_garch_t(
            self.params_,
            horizon=n_steps,
            n_paths=n_paths,
            seed=seed,
        )
