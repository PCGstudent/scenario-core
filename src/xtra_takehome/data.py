from __future__ import annotations

import numpy as np
import pandas as pd


def extract_close(frame: pd.DataFrame, ticker: str = "BZ=F") -> pd.Series:
    """Extract a close-price Series from flat or MultiIndex yfinance output."""
    if frame.empty:
        raise ValueError("Downloaded market data is empty.")

    cols = frame.columns

    if isinstance(cols, pd.MultiIndex):
        # yfinance has used both (Price, Ticker) and similar MultiIndex layouts.
        if "Close" in cols.get_level_values(0):
            close = frame.xs("Close", axis=1, level=0)
        elif "Close" in cols.get_level_values(-1):
            close = frame.xs("Close", axis=1, level=-1)
        else:
            raise KeyError("No Close field found in MultiIndex market data.")

        if isinstance(close, pd.DataFrame):
            if ticker in close.columns:
                close = close[ticker]
            elif close.shape[1] == 1:
                close = close.iloc[:, 0]
            else:
                raise ValueError("Ambiguous Close columns in downloaded data.")
        return close.astype(float).rename("close")

    if "Close" not in frame.columns:
        raise KeyError("No Close column found in downloaded market data.")

    close = frame["Close"]
    if isinstance(close, pd.DataFrame):
        if close.shape[1] != 1:
            raise ValueError("Ambiguous Close columns in downloaded data.")
        close = close.iloc[:, 0]
    return close.astype(float).rename("close")


def fetch_close(
    ticker: str,
    start: str,
    end: str,
) -> pd.Series:
    """Fetch market data in code; raw data is intentionally not persisted."""
    import yfinance as yf

    frame = yf.download(
        ticker,
        start=start,
        end=end,
        auto_adjust=False,
        progress=False,
        actions=False,
    )
    close = extract_close(frame, ticker=ticker)
    close = close[~close.index.duplicated(keep="last")].sort_index()
    close = close.dropna()

    if (close <= 0).any():
        bad = close[close <= 0]
        raise ValueError(f"Non-positive close prices prevent log returns: {bad.head()}")

    if close.shape[0] < 2520:
        raise ValueError(
            f"Expected at least ~10 trading years; received {close.shape[0]} observations."
        )
    return close


def log_returns_pct(close: pd.Series) -> pd.Series:
    """Percentage log returns, suitable for numerical GARCH fitting."""
    returns = 100.0 * np.log(close / close.shift(1))
    returns = returns.replace([np.inf, -np.inf], np.nan).dropna()
    returns.name = "return_pct"
    return returns
