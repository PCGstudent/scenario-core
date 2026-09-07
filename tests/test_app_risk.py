"""Tests for the lab's risk arithmetic.

The properties pinned here are the ones whose violation would be invisible in
the interface: a price path that silently drops its first day, a terminal return
that reports a summed log return as though it were a percentage gain, or an ES
that comes out below its VaR because a sign convention slipped.
"""

import numpy as np
import pytest

from xtra_takehome.app import risk


def _flat(n_paths: int, n_steps: int, value: float = 0.0) -> np.ndarray:
    return np.full((n_paths, n_steps), value, dtype=float)


# ---------------------------------------------------------------------------
# Price reconstruction
# ---------------------------------------------------------------------------


def test_price_paths_include_the_starting_price():
    """Column zero must be the start, so a fall on day one is visible."""
    paths = np.array([[-10.0, 0.0], [5.0, 5.0]])
    prices = risk.price_paths(paths, 100.0)

    assert prices.shape == (2, 3)
    np.testing.assert_allclose(prices[:, 0], [100.0, 100.0])


def test_price_reconstruction_matches_the_log_return_definition():
    paths = np.array([[1.0, -2.0, 0.5]])
    prices = risk.price_paths(paths, 80.0)
    expected = 80.0 * np.exp(np.cumsum(paths[0] / 100.0))
    np.testing.assert_allclose(prices[0, 1:], expected)


def test_flat_returns_leave_the_price_unchanged():
    prices = risk.price_paths(_flat(3, 10), 64.5)
    np.testing.assert_allclose(prices, 64.5)


def test_price_paths_reject_a_non_positive_start():
    with pytest.raises(ValueError):
        risk.price_paths(_flat(2, 3), 0.0)


def test_terminal_prices_are_the_last_column():
    paths = np.array([[2.0, 2.0], [-1.0, -1.0]])
    prices = risk.price_paths(paths, 50.0)
    np.testing.assert_allclose(risk.terminal_prices(paths, 50.0), prices[:, -1])


# ---------------------------------------------------------------------------
# Cumulative and terminal returns
# ---------------------------------------------------------------------------


def test_cumulative_log_returns_are_a_running_sum():
    paths = np.array([[1.0, 2.0, -0.5]])
    np.testing.assert_allclose(risk.cumulative_log_returns(paths), [[1.0, 3.0, 2.5]])


def test_terminal_return_converts_log_to_simple():
    """A summed log return of -100 is a 63% loss, not a wipeout.

    Reporting the summed log return directly as a percentage would overstate the
    loss badly, which is exactly the mistake this conversion prevents.
    """
    paths = np.array([[-50.0, -50.0]])
    terminal = risk.terminal_simple_return(paths)
    assert np.isclose(terminal[0], np.expm1(-1.0))
    assert -0.64 < terminal[0] < -0.63


def test_terminal_return_is_consistent_with_the_price_path():
    rng = np.random.default_rng(0)
    paths = rng.normal(0, 2, size=(20, 30))
    terminal = risk.terminal_simple_return(paths)
    prices = risk.terminal_prices(paths, 100.0)
    np.testing.assert_allclose(prices, 100.0 * (1.0 + terminal))


def test_flat_returns_give_zero_terminal_return():
    np.testing.assert_allclose(risk.terminal_simple_return(_flat(4, 12)), 0.0)


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------


def test_drawdown_counts_a_fall_on_the_first_day():
    """The running maximum starts at the initial level, not after day one."""
    dd = risk.max_drawdowns(np.array([[-10.0]]))
    assert np.isclose(dd[0], 1.0 - np.exp(-0.10))


def test_drawdown_is_zero_for_a_monotonic_rise():
    assert risk.max_drawdowns(np.array([[1.0, 1.0, 1.0]]))[0] == 0.0


def test_drawdown_is_bounded_and_path_dependent():
    """Same daily returns, different order, different drawdown.

    The two losing days are split by a gain in one ordering and consecutive in
    the other, so the deepest peak-to-trough excursion differs even though the
    multiset of returns -- and therefore the terminal value -- is identical.
    """
    split = np.array([[-5.0, 3.0, -5.0]])
    consecutive = np.array([[-5.0, -5.0, 3.0]])
    a = risk.max_drawdowns(split)[0]
    b = risk.max_drawdowns(consecutive)[0]

    assert 0.0 <= a < 1.0 and 0.0 <= b < 1.0
    assert b > a, "consecutive losses must produce the deeper drawdown"
    # Terminal value is the same either way; only the path differs.
    np.testing.assert_allclose(
        risk.terminal_simple_return(split), risk.terminal_simple_return(consecutive)
    )


# ---------------------------------------------------------------------------
# VaR / ES
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", [0.95, 0.99])
def test_expected_shortfall_is_at_least_var_daily(level):
    rng = np.random.default_rng(1)
    paths = rng.standard_t(df=5, size=(200, 60))
    var, es = risk.daily_var_es(paths, level)
    assert es >= var


@pytest.mark.parametrize("level", [0.95, 0.99])
def test_expected_shortfall_is_at_least_var_terminal(level):
    rng = np.random.default_rng(2)
    paths = rng.standard_t(df=5, size=(400, 40))
    var, es = risk.terminal_var_es(paths, level)
    assert es >= var


@pytest.mark.invariants  # AGENTS.md invariant 4: VaR/ES are positive loss magnitudes
def test_var_is_reported_as_a_positive_loss():
    """Losing money must produce a positive VaR, matching the repo convention."""
    losing = _flat(50, 10, -3.0)
    var, es = risk.daily_var_es(losing, 0.95)
    assert var > 0 and es > 0


@pytest.mark.invariants  # AGENTS.md invariant 24: a daily figure and a horizon figure
def test_daily_and_terminal_var_are_different_quantities():
    rng = np.random.default_rng(3)
    paths = rng.normal(0, 2, size=(500, 100))
    daily, _ = risk.daily_var_es(paths, 0.99)
    terminal, _ = risk.terminal_var_es(paths, 0.99)
    assert not np.isclose(daily, terminal, rtol=0.05), (
        "a daily figure and a horizon figure must not be interchangeable"
    )


# ---------------------------------------------------------------------------
# Probabilities
# ---------------------------------------------------------------------------


def test_probability_below_initial_on_a_known_split():
    paths = np.array([[1.0], [1.0], [-1.0], [-1.0]])
    assert risk.probability_below_initial(paths) == 0.5


def test_probability_loss_exceeds_is_monotone_in_the_threshold():
    rng = np.random.default_rng(4)
    paths = rng.normal(-0.2, 3, size=(500, 50))
    p10 = risk.probability_loss_exceeds(paths, 0.10)
    p30 = risk.probability_loss_exceeds(paths, 0.30)
    assert 0.0 <= p30 <= p10 <= 1.0


def test_probability_loss_rejects_a_negative_threshold():
    with pytest.raises(ValueError):
        risk.probability_loss_exceeds(_flat(3, 3), -0.1)


def test_probability_drawdown_exceeds_is_a_share():
    rng = np.random.default_rng(5)
    p = risk.probability_drawdown_exceeds(rng.normal(0, 2, size=(100, 40)), 0.05)
    assert 0.0 <= p <= 1.0


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


@pytest.mark.invariants  # AGENTS.md invariant 24: every metric carries its horizon
def test_every_reported_metric_declares_what_it_is_measured_over():
    rng = np.random.default_rng(6)
    metrics = risk.risk_report(rng.normal(0, 2, size=(200, 60)), 90.0)

    assert metrics
    for m in metrics:
        assert isinstance(m.kind, risk.MetricKind)
        assert m.explanation and m.unit
    kinds = {m.kind for m in metrics}
    assert kinds == set(risk.MetricKind), "all three measurement kinds should appear"


def test_terminal_percentiles_are_ordered():
    rng = np.random.default_rng(7)
    pct = risk.terminal_return_percentiles(rng.normal(0, 2, size=(500, 30)))
    values = [pct[q] for q in sorted(pct)]
    assert values == sorted(values)


def test_realized_volatility_annualization_uses_252_days():
    paths = np.array([[1.0, -1.0, 1.0, -1.0]])
    daily = risk.realized_volatility(paths, annualize=False)[0]
    annual = risk.realized_volatility(paths, annualize=True)[0]
    assert np.isclose(annual, daily * np.sqrt(252.0))


# ---------------------------------------------------------------------------
# Log vs simple returns
# ---------------------------------------------------------------------------


def test_simple_return_never_implies_losing_more_than_everything():
    """A log return of -215% is an 88% loss, not a 215% one.

    Displaying the raw log return as a percentage loss was a real defect: no
    asset can fall by more than 100%, and the tail is exactly where a reader is
    most likely to be misled.
    """
    assert risk.simple_from_log(-215.5) > -100.0
    assert np.isclose(risk.simple_from_log(-215.5), -88.41, atol=0.01)


def test_log_and_simple_returns_agree_for_ordinary_days():
    for value in (-3.0, -1.0, 0.0, 1.0, 3.0):
        assert np.isclose(risk.simple_from_log(value), value, atol=0.06)


def test_simple_from_log_is_monotone_and_bounded_below():
    grid = np.array([-500.0, -200.0, -50.0, 0.0, 50.0])
    converted = risk.simple_from_log(grid)
    assert np.all(np.diff(converted) > 0)
    assert np.all(converted > -100.0)


def test_worst_day_metric_is_reported_as_a_simple_return():
    paths = np.array([[-215.5, 0.0], [-1.0, -1.0]])
    reported = risk.worst_daily_simple_return(paths)
    raw = risk.worst_daily_return(paths)
    assert np.all(reported > -100.0)
    assert reported[0] > raw[0], "the simple return must be less alarming than the log one"


def test_risk_report_worst_day_uses_the_simple_convention():
    paths = np.full((10, 5), -1.0)
    paths[0, 0] = -300.0
    metrics = {m.name: m for m in risk.risk_report(paths, 90.0)}
    worst = metrics["Worst single day (median)"]
    assert "simple" in worst.unit
    assert worst.value > -100.0
