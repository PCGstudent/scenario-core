"""Pins the two RNG scaling properties measured during architecture analysis.

docs/architecture/IMPLEMENTATION_PLAN.md Section 2.6 records two facts about
GjrSkewTGenerator.simulate, verified directly against the fitted model before
any platform code was written:

A. Path sets ARE nested in n_paths: simulate(252, 500, seed) is bit-identical
   to simulate(252, 1000, seed)[:500]. The architecture plan's Section 21.3
   proposes relying on this later for a result cache (a smaller request can
   be served as a prefix of a larger cached run).

B. Path sets are NOT nested in horizon: simulate(100, 200, seed) is not the
   first 100 columns of simulate(252, 200, seed), because the innovation draw
   is a single (n_paths, n_steps) array per call.

Both are documented as implementation details of the current `arch`
SkewStudent draw order, not stated statistical invariants -- which is exactly
why they need a regression test now, before Section 21.3 (or anything else)
comes to depend on property A. If a future `arch` upgrade changes the draw
order, this test is what notices before the cache silently starts serving
wrong results.

The fitted-generator fixture mirrors tests/test_challenger.py's
_fitted_like_generator: a hand-built GjrSkewTGenerator with fixed parameters
and fitted state arrays, so this needs no network access and no live fit.
"""

from __future__ import annotations

import numpy as np

from xtra_takehome.challenger import GjrSkewTGenerator, GjrSkewTParams


def _fitted_like_generator() -> GjrSkewTGenerator:
    g = GjrSkewTGenerator()
    g.params_ = GjrSkewTParams(
        mu=0.02,
        omega=0.05,
        alpha=0.06,
        gamma=0.08,
        beta=0.88,
        eta=6.0,
        lam=-0.15,
    )
    g._residuals = np.array([-1.5, -0.3, 0.2, 0.8, 1.2], dtype=float)
    g._variances = np.array([1.8, 0.9, 0.7, 1.0, 1.4], dtype=float)
    return g


def test_path_sets_are_nested_in_n_paths():
    """Property A: simulate(h, 500, seed) == simulate(h, 1000, seed)[:500]."""
    g = _fitted_like_generator()
    small = g.simulate(n_steps=60, n_paths=25, seed=42)
    large = g.simulate(n_steps=60, n_paths=50, seed=42)
    np.testing.assert_array_equal(small, large[:25])


def test_path_sets_are_not_assumed_nested_in_horizon():
    """Property B: simulate(100, n, seed) is NOT simulate(252, n, seed)[:, :100].

    This is a documentation test, not a defect report: it exists so that
    property A above is never silently generalised to horizon without a
    verified reason. Both arrays are otherwise valid, finite simulations of
    the same fitted model and the same seed.
    """
    g = _fitted_like_generator()
    short = g.simulate(n_steps=20, n_paths=15, seed=7)
    long_run = g.simulate(n_steps=60, n_paths=15, seed=7)

    assert not np.array_equal(short, long_run[:, :20])
    # Sanity: both are still legitimate simulations, so a broken generator
    # cannot make this test pass by returning garbage on either side.
    assert np.isfinite(short).all()
    assert np.isfinite(long_run).all()


def test_nesting_negative_control_would_fail_on_independent_seeds():
    """Negative control: the equality check above must be able to fail.

    Comparing two DIFFERENT seeds at the same n_paths must not spuriously
    pass -- confirming the assertion in the first test is discriminating and
    not, say, comparing arrays that are equal by construction.
    """
    g = _fitted_like_generator()
    a = g.simulate(n_steps=60, n_paths=25, seed=42)
    b = g.simulate(n_steps=60, n_paths=50, seed=43)
    assert not np.array_equal(a, b[:25])
