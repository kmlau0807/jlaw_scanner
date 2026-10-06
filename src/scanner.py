"""
scanner.py
----------
Loads the HK / US universe, runs the J Law engine on each symbol, and ranks
the results into BUY and SELL buckets (top_n per market per side).

Robust by design: a single bad ticker (delisted, throttled) is skipped, not fatal.
"""
from __future__ import annotations
import os
import time
import logging
import pandas as pd
from . import data_provider as dp
from . import indicators as ind

logger = logging.getLogger("jlaw.scanner")


def load_universe(market: str, universe_dir: str = "universe") -> list[str]:
    path = os.path.join(universe_dir, f"{market}.csv")
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path)
    col = "symbol" if "symbol" in df.columns else df.columns[0]
    return [str(s).strip() for s in df[col].dropna().tolist()]


def scan_market(
    market: str,
    min_edges: int = 3,
    lookback_days: int = 180,
    min_avg_dollar_volume: float = 5_000_000,
    rs_index: str | None = None,
    throttle: float = 0.15,
    progress_cb=None,
    macd_cfg: dict | None = None,
    st_cfg: dict | None = None,
    risk_cfg: dict | None = None,
) -> list[dict]:
    """Scan one market; return a list of analysis dicts (all symbols attempted)."""
    symbols = load_universe(market)
    idx = dp.fetch_index(rs_index) if rs_index else None
    results = []
    total = len(symbols)
    for i, sym in enumerate(symbols, 1):
        try:
            df = dp.fetch(sym, period=f"{lookback_days}d", interval="1d")
            if df is None or len(df) < 30:
                logger.info("skip %s (no data)", sym)
                continue
            # liquidity gate
            adv = (df["close"] * df["volume"]).tail(20).mean()
            if adv < min_avg_dollar_volume:
                continue
            res = ind.analyze(sym, df, idx=idx, min_edges=min_edges,
                              macd_cfg=macd_cfg, st_cfg=st_cfg,
                              risk_cfg=risk_cfg)
            res["market"] = market
            results.append(res)
        except Exception as e:  # noqa: BLE001
            logger.warning("error on %s: %s", sym, e)
        if throttle:
            time.sleep(throttle)
        if progress_cb:
            progress_cb(i, total, sym)
    return results


def rank(results: list[dict], top_n: int = 10):
    """Split into (buys, sells, watches) and rank by confluence score.

    WATCH = the M.E.T.A. confluence fired but the nearest confirmed support is
    too far below price to give a tight stop, so it is deliberately NOT a BUY.
    """
    buys = [r for r in results if r["signal"] == "BUY"]
    sells = [r for r in results if r["signal"] == "SELL"]
    watches = [r for r in results if r["signal"] == "WATCH"]
    # rank buys: more edges, stronger trend, better RS
    buys.sort(
        key=lambda r: (r["score"], r["strong_trend"], r["rs"]), reverse=True
    )
    sells.sort(
        key=lambda r: (len(r["bear_edges"]), -r["rs"]), reverse=True
    )
    watches.sort(
        key=lambda r: (r["score"], r["strong_trend"], r["rs"]), reverse=True
    )
    return buys[:top_n], sells[:top_n], watches[:top_n]


def scan_all(
    markets: list[str],
    top_n: int = 10,
    min_edges: int = 3,
    lookback_days: int = 180,
    min_avg_dollar_volume: float = 5_000_000,
    rs_map: dict | None = None,
    throttle: float = 0.15,
    progress_cb=None,
    macd_cfg: dict | None = None,
    st_cfg: dict | None = None,
    risk_cfg: dict | None = None,
) -> dict:
    """Scan every requested market; return {market: {'buys':[...], 'sells':[...]}}."""
    rs_map = rs_map or {}
    out = {}
    for m in markets:
        res = scan_market(
            m,
            min_edges=min_edges,
            lookback_days=lookback_days,
            min_avg_dollar_volume=min_avg_dollar_volume,
            rs_index=rs_map.get(m),
            throttle=throttle,
            progress_cb=progress_cb,
            macd_cfg=macd_cfg,
            st_cfg=st_cfg,
            risk_cfg=risk_cfg,
        )
        b, s, w = rank(res, top_n)
        out[m] = {"buys": b, "sells": s, "watches": w, "scanned": len(res)}
    return out
