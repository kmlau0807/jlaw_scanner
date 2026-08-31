"""
data_provider.py
----------------
Fetches OHLCV data for a symbol, with an on-disk cache and a local-CSV
export fallback (so the scanner still works if a ticker fails or you are
offline).

Data sources (in priority order per symbol):
  1. TradingView  — primary source, via the `tvdatafeed` library in
                    NO-LOGIN mode (no TradingView credentials are stored
                    anywhere in this project).
  2. yfinance     — automatic fallback. Covers symbols TradingView can't
                    serve (rate-limited / not found) AND all benchmark
                    indices (^HSI, ^GSPC), which we deliberately keep on
                    Yahoo because TradingView's index symbols are fiddly.
  3. local CSV    — last-resort best-effort match against exported files.

This is the ONLY module that talks to the network. Everything else
( indicators / scanner ) is pure pandas and can run on cached data.
"""
from __future__ import annotations
import os
import re
import time
import glob
import logging
import threading
import importlib
import concurrent.futures
import pandas as pd

logger = logging.getLogger("jlaw.data")

CACHE_DIR = "cache"

# Flip to False to revert to yfinance-as-primary (TradingView only as backup).
TV_PRIMARY = True

# --- module-level singletons (one TV client reused across the whole scan) ---
_TV_CLASSES = None   # (TvDatafeed, Interval) or False if unavailable
_TV_CLIENT = None    # client instance or False if init failed
_TV_LOCK = threading.Lock()

# Optional TradingView login. None = not yet resolved; False = no creds;
# (user, pass) = credentials resolved. Resolved once, lazily, from config.ini
# [tradingview] or env vars TV_USERNAME/TV_PASSWORD. Never logged.
_TV_CREDS = None


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
    # TradingView returns a tz-AWARE index (e.g. UTC+8 / America/New_York) while
    # yfinance returns a tz-NAIVE index. If we ever align a stock against a
    # benchmark (RS calc), pandas raises "Cannot compare dtypes" and the symbol
    # is dropped. Normalize everything to tz-naive UTC so alignment always works.
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    return df


# ---------------------------------------------------------------------------
# TradingView layer
# ---------------------------------------------------------------------------
def _load_tv_classes():
    """Import the tvdatafeed classes, tolerating the on-disk dir being named
    'tvDatafeed' (capital D) on some setups. Returns (TvDatafeed, Interval)
    or False if the library is not installed."""
    global _TV_CLASSES
    if _TV_CLASSES is not None:
        return _TV_CLASSES
    errs = []
    for name in ("tvdatafeed", "tvDatafeed"):
        try:
            mod = importlib.import_module(name)
            _TV_CLASSES = (mod.TvDatafeed, mod.Interval)
            logger.info("tvdatafeed loaded (module: %s)", name)
            return _TV_CLASSES
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    _TV_CLASSES = False
    logger.warning("tvdatafeed not available (%s) -- using yfinance only", errs)
    return False


def _load_tv_creds():
    """Resolve optional TradingView credentials.

    Priority: env vars TV_USERNAME/TV_PASSWORD, then config.ini [tradingview].
    Returns (username, password) or None (stay in no-login guest mode).
    Credentials are never written to logs.
    """
    global _TV_CREDS
    if _TV_CREDS is not None:
        return _TV_CREDS or None
    user = os.environ.get("TV_USERNAME") or ""
    pwd = os.environ.get("TV_PASSWORD") or ""
    if not (user and pwd):
        try:
            import configparser
            cp = configparser.ConfigParser()
            cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.ini")
            if cp.read(cfg_path, encoding="utf-8"):
                sec = "tradingview"
                if cp.has_section(sec):
                    user = user or cp.get(sec, "username", fallback="")
                    pwd = pwd or cp.get(sec, "password", fallback="")
        except Exception:  # noqa: BLE001
            pass
    _TV_CREDS = (user, pwd) if (user and pwd) else False
    return _TV_CREDS or None


def _get_tv_client():
    """Lazily build ONE TradingView client and reuse it.

    Uses logged-in mode if [tradingview] creds are configured, otherwise
    falls back to no-login (guest) mode.
    """
    global _TV_CLIENT
    if _TV_CLIENT is not None:
        return _TV_CLIENT
    with _TV_LOCK:
        if _TV_CLIENT is not None:
            return _TV_CLIENT
        classes = _load_tv_classes()
        if not classes:
            _TV_CLIENT = False
            return False
        try:
            TvDatafeed, _ = classes
            creds = _load_tv_creds()
            if creds:
                _TV_CLIENT = TvDatafeed(username=creds[0], password=creds[1])
                logger.info("TradingView client initialized (logged-in mode)")
            else:
                # No-login mode: no TradingView username/password is used or stored.
                _TV_CLIENT = TvDatafeed()
                logger.info("TradingView client initialized (no-login mode)")
        except Exception as e:  # noqa: BLE001
            logger.warning("TradingView client init failed: %s", e)
            _TV_CLIENT = False
        return _TV_CLIENT


def _tv_symbol_map(symbol: str):
    """Map our symbol to (TradingView symbol, [exchanges to try])."""
    s = symbol.strip().upper()
    if s.startswith("^"):
        # benchmark indices -> let yfinance handle them
        return None, []
    if s.endswith(".HK"):
        return s[:-3], ["HKEX"]
    # US / generic: try the major US venues in order
    base = s.replace(".US", "")
    return base, ["NASDAQ", "NYSE", "AMEX"]


def _tv_interval(interval: str, Interval):
    return {
        "1d": Interval.in_daily,
        "1wk": Interval.in_weekly,
        "1mo": Interval.in_monthly,
    }.get(interval, Interval.in_daily)


def _period_to_nbars(period: str, default: int = 250) -> int:
    if not period:
        return default
    m = re.search(r"(\d+)\s*d", period)
    if m:
        return max(int(m.group(1)) + 5, 30)
    m = re.search(r"(\d+)\s*y", period)
    if m:
        return max(int(m.group(1)) * 250, 60)
    return default


def _tv_call(client, tv_sym: str, exch: str, iv, nb: int):
    """Call get_hist in a worker thread with a hard timeout so a stalled
    TradingView socket can never hang the whole scan."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(
            client.get_hist,
            symbol=tv_sym, exchange=exch, interval=iv, n_bars=nb,
        )
        try:
            return fut.result(timeout=25)
        except Exception:  # noqa: BLE001
            fut.cancel()
            return None


def _fetch_tradingview(symbol: str, period: str, interval: str, retries: int = 2):
    classes = _load_tv_classes()
    if not classes:
        return None
    TvDatafeed, Interval = classes
    client = _get_tv_client()
    if client is None:
        return None

    tv_sym, exchanges = _tv_symbol_map(symbol)
    if not tv_sym or not exchanges:
        return None

    nb = _period_to_nbars(period)
    iv = _tv_interval(interval, Interval)
    last_err = None

    for exch in exchanges:
        for attempt in range(retries):
            try:
                df = _tv_call(client, tv_sym, exch, iv, nb)
                if df is not None and len(df) > 20:
                    out = _normalize(df)
                    if len(out) > 20:
                        logger.info("TV ok %s @ %s (%d bars)", symbol, exch, len(out))
                        return out
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.5 * (attempt + 1))
    if last_err:
        logger.debug("TV exhausted for %s: %s", symbol, last_err)
    return None


# ---------------------------------------------------------------------------
# yfinance layer (fallback)
# ---------------------------------------------------------------------------
def _fetch_yfinance(symbol: str, period: str, interval: str, retries: int = 4):
    import yfinance as yf
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
            logger.debug("yfinance %s attempt %d failed: %s", symbol, attempt, e)
            time.sleep(wait)
    if last_err:
        logger.warning("yfinance %s failed after retries: %s", symbol, last_err)
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
        except Exception:  # noqa: BLE001
            continue
    return None


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
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
                    return _normalize(df)
            except Exception:  # noqa: BLE001
                pass

    # 2) TradingView primary (if enabled), then yfinance fallback, then CSV
    df = None
    if TV_PRIMARY:
        df = _fetch_tradingview(symbol, period, interval)
    if df is None or len(df) < 20:
        df = _fetch_yfinance(symbol, period, interval)
    if df is None or len(df) < 20:
        df = _load_local_export(symbol)

    if df is not None and len(df) > 20:
        try:
            df.to_csv(cp)
        except Exception:  # noqa: BLE001
            pass
        return df
    return None


def fetch_index(symbol: str) -> pd.DataFrame | None:
    """Convenience wrapper for benchmark indices (^GSPC, ^HSI).

    Indices are intentionally kept on yfinance (TradingView's index symbol
    names are inconsistent); this just calls fetch(), which routes '^'
    symbols straight to the yfinance fallback."""
    return fetch(symbol, period="1y", interval="1d")
