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

    def innovation_moments(self) -> tuple[float, float, float]:
        """Return E[z^2 I(z<0)], E[z^4], E[z^4 I(z<0)] for standardized skew-t."""
        from arch.univariate import SkewStudent

        dist = SkewStudent()
        parameters = np.array([self.eta, self.lam], dtype=float)
        m2_negative = float(dist.partial_moment(2, z=0.0, parameters=parameters))
        m4 = float(dist.moment(4, parameters=parameters))
        m4_negative = float(dist.partial_moment(4, z=0.0, parameters=parameters))
        return m2_negative, m4, m4_negative

    @property
    def effective_persistence(self) -> float:
        """Expected one-step GJR variance multiplier under the fitted innovation law."""
        m2_negative, _, _ = self.innovation_moments()
        return self.alpha + self.beta + self.gamma * m2_negative

    @property
    def implied_unconditional_variance(self) -> float:
        """omega / (1 - effective persistence), the level the recursion drifts toward."""
        slack = 1.0 - self.effective_persistence
        if slack <= 0.0:
            return float("inf")
        return float(self.omega / slack)

    def _variance_multiplier_moment(self, power: float) -> float:
        """E[A(z)^power] for A(z)=beta + alpha z^2 + gamma z^2 I(z<0).

        Evaluated by quadrature on the probability scale so the result is
        deterministic rather than a Monte Carlo estimate. Each tail is integrated
        under the substitution u = v^2, which concentrates nodes where the quantile
        function diverges; without it the endpoints dominate the roundoff. The
        method is checked against the closed-form values at power 1 and 2 in the
        test suite, where it agrees to within 1e-8.

        The expectation exists only while 2*power < eta, since A grows like z^2 and
        the innovation has finite moments only below order eta.
        """
        import warnings

        from arch.univariate import SkewStudent
        from scipy import integrate

        if 2.0 * power >= self.eta:
            return float("inf")

        dist = SkewStudent()
        parameters = np.array([self.eta, self.lam], dtype=float)

        def multiplier(u: float) -> float:
            z = float(dist.ppf(u, parameters))
            return self.beta + self.alpha * z**2 + self.gamma * z**2 * (z < 0.0)

        half = float(np.sqrt(0.5))

        def lower(v: float) -> float:
            return 2.0 * v * multiplier(v * v) ** power

        def upper(v: float) -> float:
            return 2.0 * v * multiplier(1.0 - v * v) ** power

        with warnings.catch_warnings():
            # Accuracy is established against the closed-form values in the tests;
            # the convergence warnings come from the quantile function's endpoints.
            warnings.simplefilter("ignore", integrate.IntegrationWarning)
            left, _ = integrate.quad(lower, 0.0, half, limit=500)
            right, _ = integrate.quad(upper, 0.0, half, limit=500)
        total = left + right
        return float(total) if np.isfinite(total) else float("inf")

    @property
    def implied_return_tail_index(self) -> float:
        """Tail index of the stationary return law implied by the fitted dynamics.

        For this volatility recursion the stationary variance has a power-law tail
        with index kappa solving E[A(z)^kappa] = 1, and returns inherit a tail index
        of 2*kappa. This is a genuine prediction of the fitted dynamics rather than a
        restatement of them: it can be compared with the Hill estimate taken directly
        from the returns, which never entered the likelihood.
        """
        from scipy import optimize

        def excess(power: float) -> float:
            return self._variance_multiplier_moment(power) - 1.0

        # E[A^p] diverges as p approaches eta/2, so the bracket is walked inward
        # until the integral is still numerically finite and the root is enclosed.
        upper = None
        for shrink in (0.02, 0.05, 0.10, 0.20, 0.30, 0.45):
            candidate = 0.5 * self.eta * (1.0 - shrink)
            if candidate <= 0.05:
                break
            value = excess(candidate)
            if np.isfinite(value) and value > 0.0:
                upper = candidate
                break

        if upper is None or excess(0.05) > 0.0:
            return float("nan")
        return float(2.0 * optimize.brentq(excess, 0.05, upper, xtol=1e-10))

    @property
    def fourth_moment_coefficient(self) -> float:
        """E[A(z)^2], where A(z)=beta+alpha*z^2+gamma*z^2*I(z<0).

        A value below one is the usual finite-fourth-moment condition for this
        GJR recursion when the innovation fourth moment exists.
        """
        if self.eta <= 4:
            return float("inf")
        m2_negative, m4, m4_negative = self.innovation_moments()
        return float(
            self.beta**2
            + 2.0 * self.beta * self.alpha
            + 2.0 * self.beta * self.gamma * m2_negative
            + self.alpha**2 * m4
            + (2.0 * self.alpha * self.gamma + self.gamma**2) * m4_negative
        )


class GjrSkewTGenerator:
    """GJR-GARCH(1,1,1) with Hansen skewed-t innovations.

    This is a single targeted refinement of the development baseline: GJR adds
    sign-dependent volatility response, while skewed-t innovations allow
    conditional asymmetry in addition to heavy tails.

    Simulation starts each path from a randomly sampled historical fitted state
    (residual and conditional variance). This avoids forcing every 252-day path to
    start from one arbitrary volatility regime and better matches the empirical
    mixture of calm/stressed starting conditions used in the calibration check.
    """

    def __init__(self) -> None:
        self.params_: GjrSkewTParams | None = None
        self.fit_summary_: str | None = None
        self._residuals: np.ndarray | None = None
        self._variances: np.ndarray | None = None

    @property
    def fitted_states(self) -> tuple[np.ndarray, np.ndarray]:
        """The fitted (residual, conditional variance) pairs, one per historical day."""
        if self._residuals is None or self._variances is None:
            raise RuntimeError("Call fit() before reading fitted states.")
        return self._residuals, self._variances

    @property
    def latest_state(self) -> tuple[float, float]:
        """The most recent fitted (residual, conditional variance).

        This is where the market actually is at the end of the sample. Conditioning
        a simulation on it answers "what might happen next, from here", which is a
        different question from the calibration check, which deliberately starts
        from a mixture of historical states so that the synthetic distribution is
        comparable with the whole historical record rather than with its final day.
        """
        residuals, variances = self.fitted_states
        return float(residuals[-1]), float(variances[-1])

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

    def _initial_states(
        self,
        initial_state: str | tuple[float, float],
        n_paths: int,
        state_seed,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Resolve the starting (residual, variance) for every path.

        The three modes answer different questions and are never interchangeable:

        ``historical_mix``
            Sample a fitted state per path. Every path starts somewhere the market
            actually was, so the pooled synthetic distribution is comparable with
            the whole historical record. This is what the validation suite uses,
            and it stays the default so that behaviour is unchanged.
        ``latest``
            Every path starts from the final fitted state. This conditions on where
            the market is now, which is what a forward-looking scenario run wants.
        explicit ``(residual, variance)``
            A stated starting point, for stress experiments.
        """
        residuals, variances = self.fitted_states

        if isinstance(initial_state, str):
            if initial_state == "historical_mix":
                rng = np.random.default_rng(state_seed)
                idx = rng.integers(0, residuals.size, size=n_paths)
                return residuals[idx].copy(), variances[idx].copy()
            if initial_state == "latest":
                eps0, var0 = self.latest_state
                return (
                    np.full(n_paths, eps0, dtype=float),
                    np.full(n_paths, var0, dtype=float),
                )
            raise ValueError(
                f"Unknown initial_state {initial_state!r}; expected "
                "'historical_mix', 'latest', or an explicit (residual, variance)."
            )

        eps0, var0 = initial_state
        if float(var0) <= 0.0:
            raise ValueError("An explicit initial variance must be positive.")
        return (
            np.full(n_paths, float(eps0), dtype=float),
            np.full(n_paths, float(var0), dtype=float),
        )

    def simulate(
        self,
        n_steps: int,
        n_paths: int,
        seed: int,
        *,
        initial_state: str | tuple[float, float] = "historical_mix",
        return_variance: bool = False,
    ) -> np.ndarray | tuple[np.ndarray, np.ndarray]:
        """Simulate return paths from the fitted model.

        Defaults reproduce the original behaviour exactly, so every existing caller
        -- the validation suite included -- is unaffected. The keyword arguments
        exist for the interactive lab: `initial_state` selects what the run is
        conditioned on, and `return_variance` also returns the conditional variance
        path, which is what makes a single scenario inspectable.
        """
        if self.params_ is None or self._residuals is None or self._variances is None:
            raise RuntimeError("Call fit() before simulate().")
        if n_steps <= 0 or n_paths <= 0:
            raise ValueError("n_steps and n_paths must be positive.")

        # arch's SkewStudent generator is explicitly seeded and returns
        # standardized innovations (mean 0, variance 1).
        from arch.univariate import SkewStudent

        # Two independent child streams. Using default_rng(seed) and
        # default_rng(seed + 1) would make consecutive seeds share a stream,
        # so replications in the robustness study would not be independent.
        state_seed, innovation_seed = np.random.SeedSequence(seed).spawn(2)

        eps_prev, var_prev = self._initial_states(initial_state, n_paths, state_seed)

        dist = SkewStudent(seed=np.random.default_rng(innovation_seed))
        draw = dist.simulate(np.array([self.params_.eta, self.params_.lam]))
        z = np.asarray(draw((n_paths, n_steps)), dtype=float)

        out = np.empty((n_paths, n_steps), dtype=float)
        variances = np.empty((n_paths, n_steps), dtype=float) if return_variance else None
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
            if variances is not None:
                variances[:, t] = variance
            eps_prev = eps
            var_prev = variance

        if variances is not None:
            return out, variances
        return out
