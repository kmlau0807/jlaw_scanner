"""
indicators.py
-------------
Pure-pandas implementation of J Law's M.E.T.S. / M.E.T.A. framework
(see JLaw_四集交易技巧總結.md for the source material).

Edges implemented (each is an *independent* signal from a different
group of market participants, so they stack into a M.E.T.A. node):

  Edge 1  Momentum & Trend   - HH/HL structure + strong-trend checklist
  Edge 2a Relative Strength  - stock vs benchmark index (跌市照妖鏡)
  Edge 2b S/R flip           - broken resistance that now acts as support
  Edge 2c MA cluster support - price hugging 10/20MA in an uptrend (zone)
  Edge 2d Channel / TL bounce- price sitting on an up-trend line (UTL)
  Edge 2e Low-volume pullback- healthy shakeout into the M.E.T.A. zone

A BUY fires when >= `min_edges` of these line up at (roughly) one price.
A SELL fires on the bearish mirror: breakdown / distribution / extended.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from . import patterns as pat

MA_FAST = 10
MA_MID = 20
MA_SLOW = 50
MA_BASE = 200


def _add_mas(df: pd.DataFrame) -> pd.DataFrame:
    for n in (MA_FAST, MA_MID, MA_SLOW, MA_BASE):
        df[f"ma{n}"] = df["close"].rolling(n).mean()
    return df


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    dn = -delta.clip(upper=0)
    gain = up.rolling(period).mean()
    loss = dn.rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def trend_structure(df: pd.DataFrame) -> dict:
    c = df["close"]
    ma50 = df["ma50"].iloc[-1]
    ma200 = df["ma200"].iloc[-1]
    price = c.iloc[-1]

    # count of new 20d highs vs new 20d lows over last 30 bars
    roll_hi = c.shift(1).rolling(20).max().shift(1)
    roll_lo = c.shift(1).rolling(20).min().shift(1)
    new_hi = (c > roll_hi).tail(30).sum()
    new_lo = (c < roll_lo).tail(30).sum()

    above_ma = price > ma50 and (pd.isna(ma200) or price > ma200)
    uptrend = bool(above_ma and new_hi >= new_lo)
    return {
        "uptrend": uptrend,
        "new_highs": int(new_hi),
        "new_lows": int(new_lo),
        "price_vs_ma50": float(price / ma50 - 1) if pd.notna(ma50) and ma50 else 0.0,
    }


def strong_trend_score(df: pd.DataFrame) -> float:
    """0..1 — how 'strong' (not just 'up') the trend is. J Law checklist."""
    c, o, v = df["close"], df["open"], df["volume"]
    score, n = 0.0, 0

    # big candles (large real body)
    body = (c - o).abs() / c
    big = body.tail(30).mean()
    score += min(big / 0.02, 1.0); n += 1

    # follow-through: up-day magnitude > down-day magnitude
    up = c > o
    up_ret = ((c / o - 1)[up]).tail(30).mean()
    dn_ret = ((o / c - 1)[~up]).tail(30).mean()
    if pd.notna(up_ret) and pd.notna(dn_ret):
        score += 1.0 if up_ret > dn_ret else 0.0; n += 1

    # volume expansion on up-days (healthy uptrend)
    vu = v[up].tail(30).mean()
    vd = v[~up].tail(30).mean()
    if pd.notna(vu) and pd.notna(vd) and vd > 0:
        score += 1.0 if vu > vd else 0.0; n += 1

    # MA proximity — hugging 10/20MA (less touching of slower MAs = stronger)
    price = c.iloc[-1]
    ma20 = df["ma20"].iloc[-1]
    if pd.notna(ma20) and ma20 > 0:
        dist = abs(price - ma20) / ma20
        score += max(0.0, 1.0 - dist / 0.08); n += 1

    # time efficiency — big move in short time
    base = c.iloc[-120] if len(c) > 120 else c.iloc[0]
    ret = price / base - 1
    score += min(max(ret / 0.4, 0.0), 1.0); n += 1

    return score / n if n else 0.0


def relative_strength(df: pd.DataFrame, idx: pd.DataFrame | None, window: int = 60) -> float:
    """Ratio-based RS over `window` days (J Law's 'leadership' / 照妖鏡).

    RS = (stock/index price ratio now) / (same ratio `window` days ago) - 1,
    expressed in %. Positive => the stock is *outperforming* the benchmark,
    which is exactly what surfaces leadership in both up and down markets.
    """
    if idx is None or len(idx) < window + 5 or len(df) < window + 5:
        return 0.0
    idx = idx.copy()
    idx.index = pd.to_datetime(idx.index)
    s = df["close"].iloc[-(window + 5):]
    s.index = pd.to_datetime(s.index)
    common = idx["close"].reindex(s.index, method="nearest").dropna()
    s = s.reindex(common.index)
    ratio = (s / common).dropna()
    if len(ratio) < 2:
        return 0.0
    return float((ratio.iloc[-1] / ratio.iloc[0] - 1) * 100.0)


def _recent_support_flip(df: pd.DataFrame) -> dict:
    """Detect a prior resistance level that price broke and is now holding as support."""
    c = df["close"]
    window = 60
    if len(df) < window:
        return {"flip": False, "level": None}
    prior = df.iloc[-window:-10]
    res_level = float(prior["high"].max())
    recent = df.iloc[-10:]
    price = c.iloc[-1]
    # broke above resistance, then pulled back but held above it
    broke = (df["close"].iloc[-15:-10] > res_level).any()
    holding = (recent["low"] >= res_level * 0.985).all() and price > res_level
    return {"flip": bool(broke and holding), "level": res_level}


def _ma_support_zone(df: pd.DataFrame) -> dict:
    c = df["close"]
    price = c.iloc[-1]
    ma10 = df["ma10"].iloc[-1]
    ma20 = df["ma20"].iloc[-1]
    if not (pd.notna(ma10) and pd.notna(ma20)):
        return {"zone": False, "support": None}
    # price within 3% of 10/20MA and above it -> pullback into support
    above = price >= ma20
    near = (abs(price - ma10) / ma10 < 0.03) or (abs(price - ma20) / ma20 < 0.03)
    support = min(ma10, ma20)
    return {"zone": bool(above and near), "support": float(support)}


def _channel_or_tl(df: pd.DataFrame) -> dict:
    """Rough up-trend-line (UTL) check: last ~40 bars make higher lows on a line."""
    c, l = df["close"], df["low"]
    if len(df) < 40:
        return {"bounce": False, "utl": None}
    seg = df.iloc[-40:]
    xs = np.arange(len(seg))
    ys = seg["low"].values.astype(float)
    # linear fit on swing lows
    coef = np.polyfit(xs, ys, 1)
    slope, intercept = coef
    if slope <= 0:
        return {"bounce": False, "utl": None}
    utl_last = slope * (len(seg) - 1) + intercept
    price = l.iloc[-1]
    bounce = bool(price >= utl_last * 0.99 and price <= utl_last * 1.03)
    return {"bounce": bounce, "utl": float(utl_last)}


def _low_volume_pullback(df: pd.DataFrame) -> bool:
    v = df["volume"]
    c = df["close"]
    # last 5 bars down/flat on below-average volume = healthy shakeout
    recent = df.tail(5)
    down = (recent["close"] <= recent["close"].shift(1)).sum() >= 3
    avg_vol = v.tail(40).mean()
    low_vol = v.tail(5).mean() < avg_vol * 0.85
    return bool(down and low_vol)


def _overbought_or_distribution(df: pd.DataFrame) -> dict:
    c = df["close"]
    price = c.iloc[-1]
    ma20 = df["ma20"].iloc[-1]
    ma50 = df["ma50"].iloc[-1]
    rsi = _rsi(c).iloc[-1]
    extended = pd.notna(ma20) and (price / ma20 - 1) > 0.15
    ob = bool(pd.notna(rsi) and rsi > 70 and extended)
    # distribution: price below slow MA after being above + vol up on down days
    below_slow = pd.notna(ma50) and price < ma50
    up = c > df["open"]
    v = df["volume"]
    vol_down = v[~up].tail(20).mean() > v[up].tail(20).mean()
    dist = bool(below_slow and vol_down)
    return {"overbought": ob, "distribution": dist, "rsi": float(rsi) if pd.notna(rsi) else None}


def analyze(
    symbol: str,
    df: pd.DataFrame,
    idx: pd.DataFrame | None = None,
    min_edges: int = 3,
) -> dict:
    """Run the full J Law analysis on one symbol. Returns a structured result."""
    df = _add_mas(df)
    ts = trend_structure(df)
    st = strong_trend_score(df)
    rs = relative_strength(df, idx)
    flip = _recent_support_flip(df)
    ma_zone = _ma_support_zone(df)
    tl = _channel_or_tl(df)
    lvp = _low_volume_pullback(df)
    weak = _overbought_or_distribution(df)
    base = pat.detect_base_pattern(df)  # cup-with-handle / VCP edge

    price = float(df["close"].iloc[-1])
    edges = []        # bullish edges fired
    bear_edges = []   # bearish edges fired

    if ts["uptrend"]:
        edges.append("Trend: HH/HL uptrend, above 50/200MA")
    if st >= 0.6:
        edges.append(f"Strong trend ({st:.2f}/1): big candles, vol-up on rallies, hugging 10/20MA")
    if rs > 0:
        edges.append(f"Relative strength +{rs:.1f}% vs index (leadership)")
    if flip["flip"]:
        edges.append(f"S/R flip: broke {flip['level']:.2f} resistance, now support")
    if ma_zone["zone"]:
        edges.append(f"MA cluster support ~{ma_zone['support']:.2f}")
    if tl["bounce"]:
        edges.append(f"On up-trend line (UTL) ~{tl['utl']:.2f}")
    if lvp:
        edges.append("Low-volume pullback (healthy shakeout)")
    # Base-pattern edge: only counts when actionable (at the pivot or breaking out)
    if base.get("found") and (base.get("at_pivot") or base.get("breakout")):
        edges.append(base["text"])

    if weak["distribution"]:
        bear_edges.append("Distribution: below 50MA on rising down-volume")
    if weak["overbought"]:
        bear_edges.append(f"Overbought: RSI {weak['rsi']:.0f}, extended >15% above 20MA")
    if ts["new_lows"] > ts["new_highs"] and not ts["uptrend"]:
        bear_edges.append("Downtrend: LH/LL structure, below 50MA")

    score = len(edges)
    signal = "HOLD"
    entry = stop = target = rr = None
    explanation = []

    if score >= min_edges and ts["uptrend"]:
        signal = "BUY"
        support = min(
            [x for x in (flip["level"], ma_zone["support"], tl["utl"]) if x]
            + [price * 0.97]
        )
        entry = round(price, 4)
        stop = round(support * 0.985, 4)
        risk = entry - stop
        target = round(entry + 3 * risk, 4)  # R:R 3:1 (J Law baseline)
        rr = round((target - entry) / risk, 2) if risk > 0 else None
        explanation = edges + [
            f"Entry ~{entry:.2f} | Stop ~{stop:.2f} (just under support, ~2-3%) | Target ~{target:.2f} (R:R {rr}:1)",
            "M.E.T.A. = multiple edges stacked at one price → only take the trade here.",
        ]
    elif len(bear_edges) >= 2 and not ts["uptrend"]:
        signal = "SELL"
        explanation = bear_edges + [
            "Consider trimming / shorting / avoiding; never catch a falling knife without a M.E.T.A. re-entry.",
        ]
    else:
        explanation = (
            edges if edges else bear_edges
        ) + ["No full M.E.T.A. confluence yet — watchlist only."]

    return {
        "symbol": symbol,
        "price": round(price, 4),
        "signal": signal,
        "score": score,
        "edges": edges,
        "bear_edges": bear_edges,
        "rs": round(rs, 2),
        "strong_trend": round(st, 3),
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "explanation": explanation,
        "base": base,
        "last_date": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else None,
    }
