from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class GjrSkewTParams:
    mu: float
    omega: float
    alpha: float
    gamma: float
    beta: float
    eta: float
    lam: float

    @property
    def approximate_persistence(self) -> float:
        """Common symmetric-innovation approximation used for discussion only."""
        return self.alpha + 0.5 * self.gamma + self.beta


class GjrSkewTGenerator:
    """GJR-GARCH(1,1,1) with Hansen skewed-t innovations.

    The challenger is intentionally a single justified refinement of the baseline:
    GJR adds sign-dependent volatility response, while skewed-t innovations allow
    conditional asymmetry in addition to heavy tails.

    Simulation starts each path from a randomly sampled *historical fitted state*
    (residual and conditional variance). This avoids forcing every 252-day path to
    start from one arbitrary volatility regime and better matches the empirical
    mixture of calm/stressed starting conditions used in the calibration check.
    """

    def __init__(self) -> None:
        self.params_: GjrSkewTParams | None = None
        self.fit_summary_: str | None = None
        self._residuals: np.ndarray | None = None
        self._variances: np.ndarray | None = None

    def fit(self, returns: pd.Series) -> "GjrSkewTGenerator":
        from arch import arch_model

        x = pd.Series(returns, dtype=float).dropna()
        model = arch_model(
            x,
            mean="Constant",
            vol="GARCH",
            p=1,
            o=1,
            q=1,
            dist="skewt",
            rescale=False,
        )
        result = model.fit(disp="off")
        p = result.params

        self.params_ = GjrSkewTParams(
            mu=float(p.get("mu", p.get("Const", 0.0))),
            omega=float(p["omega"]),
            alpha=float(p["alpha[1]"]),
            gamma=float(p["gamma[1]"]),
            beta=float(p["beta[1]"]),
            eta=float(p["eta"]),
            lam=float(p["lambda"]),
        )
        self.fit_summary_ = str(result.summary())

        residuals = np.asarray(result.resid, dtype=float)
        variances = np.asarray(result.conditional_volatility, dtype=float) ** 2
        valid = np.isfinite(residuals) & np.isfinite(variances) & (variances > 0)
        self._residuals = residuals[valid]
        self._variances = variances[valid]
        if self._residuals.size == 0:
            raise RuntimeError("No valid fitted states were produced by the GJR model.")
        return self

    def simulate(self, n_steps: int, n_paths: int, seed: int) -> np.ndarray:
        if self.params_ is None or self._residuals is None or self._variances is None:
            raise RuntimeError("Call fit() before simulate().")
        if n_steps <= 0 or n_paths <= 0:
            raise ValueError("n_steps and n_paths must be positive.")

        # arch's SkewStudent distribution owns a seedable NumPy generator and
        # returns standardized (mean 0, variance 1) innovations.
        from arch.univariate import SkewStudent

        rng = np.random.default_rng(seed)
        state_idx = rng.integers(0, self._residuals.size, size=n_paths)
        eps_prev = self._residuals[state_idx].copy()
        var_prev = self._variances[state_idx].copy()

        dist = SkewStudent(seed=np.random.default_rng(seed + 1))
        draw = dist.simulate(np.array([self.params_.eta, self.params_.lam]))
        z = np.asarray(draw((n_paths, n_steps)), dtype=float)

        out = np.empty((n_paths, n_steps), dtype=float)
        p = self.params_
        for t in range(n_steps):
            leverage = (eps_prev < 0.0).astype(float)
            variance = (
                p.omega
                + p.alpha * eps_prev**2
                + p.gamma * leverage * eps_prev**2
                + p.beta * var_prev
            )
            variance = np.maximum(variance, 1e-12)
            eps = np.sqrt(variance) * z[:, t]
            out[:, t] = p.mu + eps
            eps_prev = eps
            var_prev = variance

        return out
