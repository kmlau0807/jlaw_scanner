"""
data_provider.py
----------------
Fetches OHLCV data for a symbol from yfinance, with an on-disk cache and a
local-CSV export fallback (so the scanner still works if a ticker fails to
fetch or you are offline).

Designed to be the ONLY module that talks to the network. Everything else
( indicators / scanner ) is pure pandas and can run on cached data.
"""
from __future__ import annotations
import os
import time
import glob
import logging
import pandas as pd
import yfinance as yf

logger = logging.getLogger("jlaw.data")

CACHE_DIR = "cache"


def _cache_path(symbol: str, interval: str = "1d") -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe = symbol.replace(".", "_").replace("^", "")
    return os.path.join(CACHE_DIR, f"{safe}_{interval}.csv")


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    df = df[keep]
    df = df.dropna(subset=["close"])
    return df


def fetch(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
    use_cache: bool = True,
    max_age_hours: float = 20.0,
) -> pd.DataFrame | None:
    """Return a cleaned OHLCV DataFrame, or None on total failure."""
    cp = _cache_path(symbol, interval)

    # 1) cache hit (fresh)
    if use_cache and os.path.exists(cp):
        age_h = (time.time() - os.path.getmtime(cp)) / 3600.0
        if age_h < max_age_hours:
            try:
                df = pd.read_csv(cp, index_col=0, parse_dates=True)
                df.index = pd.to_datetime(df.index)
                if len(df) > 20:
                    return df
            except Exception:
                pass

    # 2) live fetch with retry/backoff (Yahoo throttles with 429)
    df = _fetch_yfinance(symbol, period, interval)
    if df is None or len(df) < 20:
        df = _load_local_export(symbol)

    if df is not None and len(df) > 20:
        try:
            df.to_csv(cp)
        except Exception:
            pass
        return df
    return None


def _fetch_yfinance(symbol: str, period: str, interval: str, retries: int = 4):
    last_err = None
    for attempt in range(retries):
        try:
            tk = yf.Ticker(symbol)
            df = tk.history(
                period=period, interval=interval, auto_adjust=True, actions=False
            )
            if df is not None and len(df) > 20:
                return _normalize(df)
        except Exception as e:  # noqa: BLE001
            last_err = e
            code = getattr(e, "code", None)
            wait = 2.0 * (attempt + 1)
            if code == 429:
                wait += 3.0
            logger.debug("fetch %s attempt %d failed: %s", symbol, attempt, e)
            time.sleep(wait)
    if last_err:
        logger.warning("fetch %s failed after retries: %s", symbol, last_err)
    return None


def _load_local_export(symbol: str) -> pd.DataFrame | None:
    """Best-effort match against any *.csv export lying around the workspace."""
    base = symbol.replace(".HK", "").replace("^", "").lower()
    candidates = glob.glob("*.csv") + glob.glob("exports/*.csv")
    for path in candidates:
        if base not in os.path.basename(path).lower():
            continue
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            return _normalize(df)
        except Exception:
            continue
    return None


def fetch_index(symbol: str) -> pd.DataFrame | None:
    """Convenience wrapper for benchmark indices (^GSPC, ^HSI)."""
    return fetch(symbol, period="1y", interval="1d")
