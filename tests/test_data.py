import numpy as np
import pandas as pd

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
