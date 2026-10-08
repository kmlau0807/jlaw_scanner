"""meta_edges.py
================
Port of meta-screener's M.E.T.A. edge detectors into jlaw_scanner, so the
single *unified* engine keeps jlaw's risk layer (nearest-support stop, risk
cap -> WATCH, R:R 3:1, BUY/SELL) but gains meta's richer, multi-timeframe
edge set:

  * S/R zones (clustered, not a line) + near-support with an ATR stop
  * weekly higher-timeframe alignment (genuine multi-timeframe filter)
  * volume-confirmed breakout + retest of a flipped zone
  * 照妖鏡 relative strength: 3 windows + "market down, stock up" divergence
  * RSI / MACD bullish divergence (momentum deceleration)
  * richer low-volume pullback (depth from swing high + leg retracement)
  * standalone volume-fuel edge (up-day vol vs down-day vol)

Threshold constants mirror meta-screener/config.py exactly so behaviour is
identical. The functions are self-contained (pandas/numpy only) and take a
daily OHLCV frame plus, where needed, the benchmark frame or prebuilt zones.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- thresholds
# Mirror meta-screener/config.py so the two engines agree.
ZONE_LOOKBACK = 250
ZONE_TOL = 0.02
ZONE_MIN_TOUCHES = 2
NEAR_SUPPORT_PCT = 0.025
TREND_LOOKBACK = 20
GREEN_RATIO_MIN = 0.55
RET20_MIN = 0.08
TREND_MIN_CHECKS = 3
UPDOWN_VOL_RATIO = 1.15
PULLBACK_MAX_DEPTH = 0.15
PULLBACK_WINDOW = 15
PULLBACK_MAX_RETRACE = 0.62
HTF_EMA_FAST = 10
HTF_EMA_SLOW = 30
BREAKOUT_LOOKBACK = 15
BREAKOUT_VOL_MULT = 1.3
RETEST_BAND = 0.03
RS_WINDOWS = [20, 60, 120]
RS_MIN_OUTPERFORM = 2
DIVERGENCE_LOOKBACK = 180
DIVERGENCE_MIN_BARS = 10


# ---------------------------------------------------------------- helpers
def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def find_pivots(df: pd.DataFrame, k: int = 3):
    """Return (pivot_highs, pivot_lows) as [(date, price), ...].

    A pivot high's High is the strict max of its +/-k window.
    """
    ph, pl = [], []
    highs, lows = df["high"], df["low"]
    n = len(df)
    for i in range(k, n - k):
        wh = highs.iloc[i - k: i + k + 1]
        if highs.iloc[i] >= wh.max() and (wh == highs.iloc[i]).sum() == 1:
            ph.append((df.index[i], float(highs.iloc[i])))
        wl = lows.iloc[i - k: i + k + 1]
        if lows.iloc[i] <= wl.min() and (wl == lows.iloc[i]).sum() == 1:
            pl.append((df.index[i], float(lows.iloc[i])))
    return ph, pl


def build_zones(df: pd.DataFrame, lookback=ZONE_LOOKBACK, tol=ZONE_TOL,
                min_touches=ZONE_MIN_TOUCHES, max_width=0.045):
    """Cluster swing pivots into horizontal support/resistance zones.

    Returns list of {'low','high','mid','touches','last_touch'} sorted by low.
    A zone is a *price area*, not a line (S/R is a zone per J Law). Clusters
    grow only while price stays within `tol` of the cluster mean and the total
    width stays under `max_width` (prevents chained mega-zones).
    """
    d = df.tail(lookback)
    if len(d) < 30:
        return []
    ph, pl = find_pivots(d, k=3)
    pts = sorted(
        [(p, dt) for dt, p in ph] + [(p, dt) for dt, p in pl],
        key=lambda x: x[0],
    )
    zones = []
    cluster = []
    for price, dt in pts:
        if cluster:
            mean = float(np.mean([p for p, _ in cluster]))
            too_far = price - mean > tol * price
            too_wide = price - min(p for p, _ in cluster) > max_width * price
            if too_far or too_wide:
                zones.append(cluster)
                cluster = []
        cluster.append((price, dt))
    if cluster:
        zones.append(cluster)

    out = []
    for z in zones:
        prices = [p for p, _ in z]
        out.append({
            "low": float(min(prices)),
            "high": float(max(prices)),
            "mid": float(np.mean(prices)),
            "touches": len(z),
            "last_touch": max(dt for _, dt in z),
        })
    out = [z for z in out if z["touches"] >= min_touches]
    out.sort(key=lambda z: z["low"])
    return out


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs_ = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs_)
    return out.fillna(100.0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Return (macd_line, signal_line, histogram) as Series."""
    ef = ema(series, fast)
    es = ema(series, slow)
    macd_line = ef - es
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _close(df):
    return float(df["close"].iloc[-1])


# ---------------------------------------------------------------- Edge: volume fuel
def edge_volume_fuel(df: pd.DataFrame):
    """Volume is the fuel: up-days should carry more volume than down-days."""
    v = df["volume"]
    body = df["close"] - df["open"]
    dnv = float(v[(body < 0)].tail(TREND_LOOKBACK).mean())
    upv = float(v[(body > 0)].tail(TREND_LOOKBACK).mean())
    ratio = upv / dnv if dnv > 0 else np.inf
    return {
        "active": bool(ratio >= UPDOWN_VOL_RATIO),
        "up_down_vol_ratio": round(float(ratio), 2) if np.isfinite(ratio) else 99.0,
    }


# ---------------------------------------------------------------- Edge: low-vol pullback
def edge_low_vol_pullback(df: pd.DataFrame):
    """Healthy pullback: shallow decline from a recent swing high on
    contracting volume, retracing < ~62% of the prior up-leg."""
    close = df["close"]
    win = df.tail(PULLBACK_WINDOW)
    hi_date = win["close"].idxmax()
    hi = float(win["close"].max())
    last = float(close.iloc[-1])

    pos = df.index.get_loc(df.index[-1]) - df.index.get_loc(hi_date)
    if pos < 1:
        return {"active": False, "reason": "at highs / no pullback"}

    depth = (hi - last) / hi
    in_pullback = 0 < depth <= PULLBACK_MAX_DEPTH

    vol50 = float(df["volume"].rolling(50).mean().iloc[-1])
    seg = df.loc[hi_date:]
    pull_vol = float(seg["volume"].mean())
    low_vol = vol50 > 0 and pull_vol < vol50

    _, pl = find_pivots(df.tail(120), 3)
    prior_lows = [p for dt, p in pl if dt < hi_date]
    retrace, retrace_ok = None, True
    if prior_lows and hi > prior_lows[-1]:
        leg = hi - prior_lows[-1]
        if leg > 0:
            retrace = (hi - last) / leg
            retrace_ok = retrace <= PULLBACK_MAX_RETRACE

    active = bool(in_pullback and low_vol and retrace_ok)
    return {
        "active": active,
        "depth": round(depth, 3),
        "days_since_high": int(pos),
        "pull_vol_vs_avg": round(pull_vol / vol50, 2) if vol50 > 0 else None,
        "retrace": round(retrace, 2) if retrace is not None else None,
    }


# ---------------------------------------------------------------- Edge: near support (+ATR stop)
def edge_near_support(df: pd.DataFrame, zones):
    """Price sitting on / just above a support zone -> tight-stop entry."""
    if not zones:
        return {"active": False}
    close = float(df["close"].iloc[-1])
    low = float(df["low"].iloc[-1])
    a = float(atr(df).iloc[-1])

    below = [z for z in zones if z["high"] <= close * 1.005]
    if not below:
        return {"active": False}
    z = max(below, key=lambda x: x["high"])  # nearest support below price
    dist = (close - z["high"]) / close
    dipped = low <= z["high"] and close >= z["low"]  # wick into zone, held
    active = bool(dist <= NEAR_SUPPORT_PCT or dipped)
    stop = z["low"] - 0.5 * a
    risk = (close - stop) / close if stop < close else None
    return {
        "active": active,
        "zone_low": round(z["low"], 2),
        "zone_high": round(z["high"], 2),
        "touches": z["touches"],
        "dist_pct": round(dist, 3),
        "wick_into_zone": bool(dipped),
        "suggested_stop": round(stop, 2),
        "risk_pct": round(risk, 3) if risk is not None else None,
    }


# ---------------------------------------------------------------- Edge: weekly HTF alignment
def edge_weekly_htf(df: pd.DataFrame):
    """Weekly chart must be in an uptrend (higher timeframe dominance)."""
    try:
        if not isinstance(df.index, pd.DatetimeIndex):
            return {"active": False}
        w = df.resample("W-FRI").agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}
        ).dropna(subset=["close"])
    except Exception:
        return {"active": False}
    if len(w) < HTF_EMA_SLOW + 5:
        return {"active": False}
    c = w["close"]
    ef = ema(c, HTF_EMA_FAST)
    es = ema(c, HTF_EMA_SLOW)
    up = (
        c.iloc[-1] > ef.iloc[-1]
        and ef.iloc[-1] > es.iloc[-1]
        and ef.iloc[-1] >= ef.iloc[-HTF_EMA_FAST]
    )
    return {"active": bool(up)}


# ---------------------------------------------------------------- Edge: breakout + retest
def edge_breakout_retest(df: pd.DataFrame, zones):
    """Volume-confirmed breakout above a zone; plus retest of the flipped
    zone (old resistance becomes new support)."""
    if not zones:
        return {"breakout": {"active": False}, "retest": {"active": False}}

    close, vol = df["close"], df["volume"]
    vol50 = df["volume"].rolling(50).mean()
    last_pos = len(df) - 1

    for i in range(max(1, len(df) - BREAKOUT_LOOKBACK), len(df)):
        c, prev = float(close.iloc[i]), float(close.iloc[i - 1])
        for z in zones:
            crossed = prev <= z["high"] and c > z["high"] and prev >= z["low"] * 0.9
            if not crossed:
                continue
            vm = float(vol.iloc[i] / vol50.iloc[i]) if vol50.iloc[i] > 0 else 0.0
            if vm >= BREAKOUT_VOL_MULT:
                bk = {
                    "active": True,
                    "date": str(df.index[i].date()),
                    "bars_ago": last_pos - i,
                    "zone_low": round(z["low"], 2),
                    "zone_high": round(z["high"], 2),
                    "vol_mult": round(vm, 2),
                }
                cl = _close(df)
                lo, hi = z["low"], z["high"]
                in_zone = lo * (1 - RETEST_BAND) <= cl <= hi * (1 + RETEST_BAND)
                retest_active = bool(
                    last_pos - i >= 1 and in_zone and cl >= lo * 0.97
                )
                return {
                    "breakout": bk,
                    "retest": {
                        "active": retest_active,
                        "zone_low": round(z["low"], 2),
                        "zone_high": round(z["high"], 2),
                        "date": bk["date"],
                    },
                }
    return {"breakout": {"active": False}, "retest": {"active": False}}


# ---------------------------------------------------------------- Edge: relative strength (照妖鏡)
def edge_rs_leadership(df: pd.DataFrame, bench_df: pd.DataFrame | None):
    """Relative strength vs benchmark (J Law: 大盤跌佢升 = 照妖鏡).

    A stock is a leader when it OUTPERFORMS the market over multiple windows.
    The strongest signal is the benchmark DOWN while the stock is UP — that
    divergence exposes hidden strength. Compare over RS_WINDOWS lookbacks and
    count how many windows the stock beats the benchmark.
    """
    if bench_df is None or len(bench_df) < RS_WINDOWS[-1]:
        return {"active": False, "reason": "no benchmark", "score": 0}

    try:
        if not isinstance(df.index, pd.DatetimeIndex):
            return {"active": False, "reason": "no datetime index", "score": 0}
        common = df.index.intersection(bench_df.index)
    except Exception:
        return {"active": False, "reason": "index error", "score": 0}
    if len(common) < RS_WINDOWS[-1] + 5:
        return {"active": False, "reason": "insufficient date overlap", "score": 0}

    s = df["close"].reindex(common).astype(float)
    b = bench_df["close"].reindex(common).astype(float)

    out: dict = {}
    beats = 0
    down_up = False
    last_rs = None
    for w in RS_WINDOWS:
        if len(common) <= w:
            continue
        s_ret = float(s.iloc[-1] / s.iloc[-(w + 1)] - 1)
        b_ret = float(b.iloc[-1] / b.iloc[-(w + 1)] - 1)
        rs = s_ret - b_ret
        out[f"rs_{w}"] = round(rs, 3)
        out[f"stock_{w}"] = round(s_ret, 3)
        out[f"bench_{w}"] = round(b_ret, 3)
        if rs > 0:
            beats += 1
        if w == RS_WINDOWS[-1]:
            last_rs = rs
        if b_ret < 0 and s_ret > 0:  # 照妖鏡
            down_up = True

    out["beats"] = beats
    out["down_market_up_stock"] = bool(down_up)
    if down_up:
        beats = max(beats, 2)
    out["score"] = beats
    out["active"] = bool(
        beats >= RS_MIN_OUTPERFORM and (last_rs is None or last_rs > 0)
    ) or bool(down_up)
    return out


# ---------------------------------------------------------------- Edge: RSI/MACD divergence
def edge_divergence(df: pd.DataFrame, lookback=DIVERGENCE_LOOKBACK,
                    min_bars=DIVERGENCE_MIN_BARS):
    """RSI / MACD divergence — momentum deceleration hidden in price.

    Bullish (底背離, a BUY edge for this long-biased framework):
        price makes a lower low, but the indicator makes a HIGHER low ->
        downside momentum is fading -> a reversal up is more likely.
    Bearish (頂背離) is reported as a warning only (never scored here).
    """
    out = {
        "active": False, "score": 0,
        "bull_rsi": False, "bull_macd": False,
        "bear_rsi": False, "bear_macd": False,
        "bull_rsi_date": None, "bull_macd_date": None,
        "bear_rsi_date": None, "bear_macd_date": None,
        "detail": "",
    }
    if len(df) < 60:
        return out

    close = df["close"]
    r = rsi(close, 14)
    m, s_, h = macd(close)

    ph, pl = find_pivots(df.tail(lookback), 3)

    def _vals(pivots):
        v = []
        for dt, price in pivots:
            if dt in r.index:
                v.append((dt, price, float(r.loc[dt]), float(m.loc[dt])))
        return v

    plv, phv = _vals(pl), _vals(ph)

    if len(plv) >= 2:
        d1, p1, r1, mc1 = plv[-2]
        d2, p2, r2, mc2 = plv[-1]
        gap_ok = (d2 - d1).days >= min_bars
        if gap_ok and p2 < p1:
            if r2 > r1:
                out["bull_rsi"] = True
                out["bull_rsi_date"] = d2
            if mc2 > mc1:
                out["bull_macd"] = True
                out["bull_macd_date"] = d2

    if len(phv) >= 2:
        d1, p1, r1, mc1 = phv[-2]
        d2, p2, r2, mc2 = phv[-1]
        gap_ok = (d2 - d1).days >= min_bars
        if gap_ok and p2 > p1:
            if r2 < r1:
                out["bear_rsi"] = True
                out["bear_rsi_date"] = d2
            if mc2 < mc1:
                out["bear_macd"] = True
                out["bear_macd_date"] = d2

    out["active"] = out["bull_rsi"] or out["bull_macd"]
    out["score"] = 1 if out["active"] else 0

    bits = []
    if out["bull_rsi"]:
        bits.append("RSI 底背離（價格新低、RSI 唔新低＝下跌動力減弱）")
    if out["bull_macd"]:
        bits.append("MACD 底背離（價格新低、MACD 唔新低）")
    if out["bear_rsi"]:
        bits.append("RSI 頂背離警告（價格新高、RSI 唔新高＝上升動力減弱）")
    if out["bear_macd"]:
        bits.append("MACD 頂背離警告（價格新高、MACD 唔新高）")
    out["detail"] = "；".join(bits) if bits else "無明顯背離"
    return out
