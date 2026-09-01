import numpy as np
import pandas as pd
import pytest

from xtra_takehome.data import extract_close, log_returns_pct


def test_extract_close_flat():
    frame = pd.DataFrame(
        {"Open": [70.0, 71.0], "Close": [70.5, 72.0]},
        index=pd.date_range("2026-01-01", periods=2),
    )
    close = extract_close(frame)
    assert list(close) == [70.5, 72.0]
    assert close.name == "close"


def test_extract_close_multiindex_price_ticker():
    cols = pd.MultiIndex.from_tuples(
        [("Open", "BZ=F"), ("Close", "BZ=F")]
    )
    frame = pd.DataFrame(
        [[70.0, 70.5], [71.0, 72.0]],
        columns=cols,
        index=pd.date_range("2026-01-01", periods=2),
    )
    close = extract_close(frame, ticker="BZ=F")
    assert list(close) == [70.5, 72.0]


def test_log_returns_pct():
    close = pd.Series([100.0, 101.0, 99.0])
    r = log_returns_pct(close)
    expected = 100.0 * np.log(np.array([101.0 / 100.0, 99.0 / 101.0]))
    np.testing.assert_allclose(r.to_numpy(), expected)


def test_fetch_close_uses_the_cache_without_touching_the_network(tmp_path):
    """A clean-clone run fetches once; the other entry points read the cache."""
    from xtra_takehome.data import _cache_path, fetch_close

    index = pd.bdate_range("2010-01-04", periods=2600)
    close = pd.Series(
        70.0 + np.linspace(0.0, 30.0, index.size), index=index, name="close"
    )
    cache_file = _cache_path("BZ=F", "2010-01-01", "2026-09-01", tmp_path)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    close.to_csv(cache_file)

    # yfinance is never imported: a network call here would fail the test offline.
    cached = fetch_close("BZ=F", "2010-01-01", "2026-09-01", cache_dir=tmp_path)
    assert cached.shape == close.shape
    np.testing.assert_allclose(cached.to_numpy(), close.to_numpy())


def test_fetch_close_rejects_a_short_cached_series(tmp_path):
    from xtra_takehome.data import _cache_path, fetch_close

    index = pd.bdate_range("2020-01-01", periods=100)
    cache_file = _cache_path("BZ=F", "2020-01-01", "2020-06-01", tmp_path)
    pd.Series(70.0, index=index, name="close").to_csv(cache_file)

    with pytest.raises(ValueError, match="10 trading years"):
        fetch_close("BZ=F", "2020-01-01", "2020-06-01", cache_dir=tmp_path)
