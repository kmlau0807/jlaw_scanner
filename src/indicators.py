"""
indicators.py
-------------
Pure-pandas implementation of J Law's M.E.T.S. / M.E.T.A. framework
(see JLaw_四集交易技巧總結.md for the source material).

Edges implemented (each is an *independent* signal from a different
group of market participants, so they stack into a M.E.T.A. node).
This is the *unified* engine: jlaw's original edges plus meta-screener's
richer, multi-timeframe set (ported in src/meta_edges.py) — see
`merge_meta_edges` design note. jlaw's risk layer (nearest-support stop,
risk cap -> WATCH, R:R 3:1, BUY/SELL) is preserved.

  jlaw originals
    - Trend structure      - HH/HL uptrend, above 50/200MA (mandatory gate)
    - Strong trend (F1-F6)  - EP1 six-feature checklist
    - Relative strength     - 60d ratio vs benchmark (leadership)
    - S/R flip              - broken resistance now acting as support
    - MA cluster support    - price hugging 10/20MA in an uptrend
    - Up-trend line bounce  - price sitting on a UTL
    - Low-volume pullback   - healthy shakeout (now meta's richer version)
    - Base pattern          - cup-with-handle / VCP (patterns.py)
  meta_edges (multi-timeframe, ported)
    - S/R zones + near-support - clustered zones, ATR-based stop (feeds _pick_stop)
    - Weekly HTF alignment  - higher-timeframe dominance filter
    - Volume breakout       - volume-confirmed zone breakout
    - Retest of flipped zone- old resistance -> new support
    - 照妖鏡 RS leadership   - 3 windows + "market down, stock up"
    - RSI/MACD divergence   - bullish momentum deceleration (底背離)
    - Volume fuel           - up-day vol vs down-day vol

A BUY fires when >= `min_edges` of these line up at (roughly) one price.
A SELL fires on the bearish mirror: breakdown / distribution / extended.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from . import patterns as pat
from . import meta_edges as me

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


# ---- EP1 強勁趨勢六大特徵 (Strong-Trend Six Features) ----
# 對應 J Law EP1「強勁趨勢的特徵」：
#   F1  上升趨勢結構 (HH/HL)         — 強勁趨勢的前提（必須為 True）
#   F2  大陽/大陰燭 + 裂口           — 升跌幅顯著
#   F3  上升大成交 / 下跌低量拉回     — 量價配合（強勢特徵）
#   F4  燭身少重疊 + follow-through   — 延續性強、非震盪
#   F5  10/20MA 支持、不碰 20 以上 MA — 強勢貼著快線、不回落慢線
#   F6  時間效益高（短時間大升幅）    — 幾日就賺 20–30%
# 全部閾值可由 config.ini [strong_trend] 覆寫。
DEFAULT_STRONG_TREND_CFG = {
    "win": 30,                # F2–F4 的燭身/量/延續窗口
    "struct_win": 60,         # F1 的趨勢結構窗口
    "body_thresh": 0.02,      # F2: 平均真實實體佔比門檻
    "gap_thresh": 0.01,       # F2: 裂口門檻
    "vol_ratio_thresh": 1.0,  # F3: 升日量 / 跌日量 比
    "time_eff_win": 20,       # F6: 時間效益窗口
    "time_eff_ret": 0.20,     # F6: 窗口內回報門檻 (20%)
    "min_features": 5,        # 需要幾項特徵（含 F1）才算強勁
    "score_thresh": 0.6,      # 綜合分門檻（備用）
}


def strong_trend_features(df: pd.DataFrame, cfg: dict | None = None) -> dict:
    """EP1 強勁趨勢六大特徵 —— 可解釋檢測器。

    Returns a dict with 6 sub-scores (0..1; F1 is boolean), plus:
      count       = 幾項特徵達標 (>=0.5，F1 需為 True)
      score       = 綜合分 0..1（與舊 strong_trend_score 兼容）
      is_strong   = F1 為 True 且 count >= min_features
    """
    cfg = {**DEFAULT_STRONG_TREND_CFG, **(cfg or {})}
    # config.ini / 傳入 dict 的值可能是字串，強制轉型避免 float/str 運算錯誤
    for _k in ("win", "struct_win", "time_eff_win", "min_features"):
        cfg[_k] = int(cfg[_k])
    for _k in ("body_thresh", "gap_thresh", "vol_ratio_thresh",
               "time_eff_ret", "score_thresh"):
        cfg[_k] = float(cfg[_k])
    df = _add_mas(df)  # 確保 ma10/20/50/200 存在（被 analyze 呼叫時重算無害）
    c, o, h, l, v = df["close"], df["open"], df["high"], df["low"], df["volume"]
    price = float(c.iloc[-1])
    win = int(cfg["win"])
    out: dict = {}

    # ---- F1 上升趨勢結構 (HH/HL) ----
    ma50 = df["ma50"].iloc[-1]
    ma200 = df["ma200"].iloc[-1]
    above_ma = bool(price > ma50 and (pd.isna(ma200) or price > ma200))
    roll_hi = c.shift(1).rolling(20).max().shift(1)
    roll_lo = c.shift(1).rolling(20).min().shift(1)
    new_hi = int((c > roll_hi).tail(int(cfg["struct_win"])).sum())
    new_lo = int((c < roll_lo).tail(int(cfg["struct_win"])).sum())
    f1 = bool(above_ma and new_hi >= new_lo)
    out["f1_trend_structure"] = f1

    # ---- F2 大陽/大陰燭 + 裂口 ----
    body = (c - o).abs() / o
    avg_body = float(body.tail(win).mean())
    gap_up = (o / c.shift(1) - 1).clip(lower=0)
    gap_dn = (c.shift(1) / o - 1).clip(lower=0)
    avg_gap = float(max(gap_up.tail(win).mean(), gap_dn.tail(win).mean()))
    f2_body = min(avg_body / cfg["body_thresh"], 1.0)
    f2_gap = min(avg_gap / cfg["gap_thresh"], 1.0)
    f2 = 0.6 * f2_body + 0.4 * f2_gap
    out["f2_big_candles_gaps"] = round(f2, 3)

    # ---- F3 上升大成交 / 下跌低量拉回 ----
    up = c > o
    vu = float(v[up].tail(win).mean())
    vd = float(v[~up].tail(win).mean())
    if vd > 0:
        ratio = vu / vd
        f3 = min(ratio / (2 * cfg["vol_ratio_thresh"]), 1.0)  # 升日量是跌日量 2 倍以上 → 1.0
    else:
        f3 = 0.0
    out["f3_volume_signature"] = round(f3, 3)

    # ---- F4 燭身少重疊 + follow-through ----
    # 4a follow-through: 連續兩日同向上漲（收盤>前收）的 bar 佔比
    up_close = (c > c.shift(1)).astype(int).tail(win).values
    ft = sum(1 for i in range(1, len(up_close)) if up_close[i] and up_close[i - 1])
    f4_ft = min(ft / (win * 0.30), 1.0)
    # 4b 燭身少重疊: 昨日實體區間與今日實體區間不重疊的佔比（gap 或跳空）
    prev_body_hi = c.shift(1)
    prev_body_lo = o.shift(1)
    no_overlap = ((o >= prev_body_hi) | (c <= prev_body_lo)).tail(win - 1)
    f4_ov = float(no_overlap.mean()) if len(no_overlap) else 0.0
    f4 = 0.5 * f4_ft + 0.5 * f4_ov
    out["f4_follow_through"] = round(f4, 3)

    # ---- F5 10/20MA 支持，不碰 20 以上 MA ----
    # EP1 原意：強勢股要麼「貼著 10/20MA 當支持」，要麼「強到連 10MA 都不碰、
    # 遠離 20 以上慢線」。兩者都算強 —— 故以「價格在多條快/中 MA 之上 +
    # 多頭排列(10>20>50) + 20MA 向上」為判據，而非要求貼近。
    ma10 = df["ma10"].iloc[-1]
    ma20 = df["ma20"].iloc[-1]
    ma50v = df["ma50"].iloc[-1]
    if pd.notna(ma10) and pd.notna(ma20) and pd.notna(ma50v) and ma20 > 0:
        # 價格需站在 20MA/50MA 之上（允許健康回測 10MA；若跌破 20MA 視為較深回調）
        above_fast = bool(price >= ma20 and price >= ma50v)
        aligned = bool(ma10 >= ma20 >= ma50v)          # 多頭排列
        ma20_s = df["ma20"]
        if len(ma20_s) > 10:
            slope = (ma20_s.iloc[-1] - ma20_s.iloc[-11]) / ma20_s.iloc[-11]
        else:
            slope = 0.0
        rising = bool(slope > 0)
        f5 = 1.0 if (above_fast and aligned and rising) else 0.0
    else:
        f5 = 0.0
    out["f5_ma_hugging"] = round(f5, 3)

    # ---- F6 時間效益高（短時間大升幅）----
    base_idx = -int(cfg["time_eff_win"])
    if len(c) > -base_idx:
        base = float(c.iloc[base_idx])
        ret = price / base - 1 if base else 0.0
    else:
        ret = price / float(c.iloc[0]) - 1 if c.iloc[0] else 0.0
    f6 = min(max(ret / cfg["time_eff_ret"], 0.0), 1.0)
    out["f6_time_efficiency"] = round(f6, 3)

    # ---- aggregate ----
    feats = [f1, f2, f3, f4, f5, f6]
    count = sum(1 for x in feats if (x if isinstance(x, bool) else x >= 0.5))
    score = (1.0 if f1 else 0.0) + f2 + f3 + f4 + f5 + f6
    score = score / 6.0
    min_f = int(cfg["min_features"])
    out["count"] = count
    out["score"] = round(score, 3)
    out["is_strong"] = bool(f1 and count >= min_f)
    return out


def strong_trend_score(df: pd.DataFrame) -> float:
    """Backward-compatible shim: aggregate 0..1 score (EP1 six-feature version)."""
    return strong_trend_features(df)["score"]


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


def _macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9):
    """Standard MACD (12/26/9 by default). Returns (macd_line, signal_line, histogram)."""
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def macd_state(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    energy_window: int = 60,
    small_energy_thresh: float = 0.30,
) -> dict:
    """MACD diagnostics implementing the two novel filters from the
    'best MACD strategy' tutorial (see youtube_OLm9w1vbLmU/summary.md):

      * Histogram-size filter — |hist| normalised by its trailing max. A *small*
        histogram means weak momentum, so a fresh cross is a *reliable* reversal
        signal; a *large* histogram means strong trend, so the cross is *unreliable*
        (explains why death crosses fail inside a bull run).
      * The EMA200 trend gate is applied by the caller (trend_structure already
        tracks MA200), this function just reports the cross + energy so edges can
        be gated on trend direction.

    Returns: cross_up, cross_dn, energy (0..1), small_energy, above_zero.
    """
    macd_line, signal_line, hist = _macd(df, fast, slow, signal)
    cross_up = bool(
        (macd_line > signal_line).iloc[-1] and (macd_line <= signal_line).iloc[-2]
    )
    cross_dn = bool(
        (macd_line < signal_line).iloc[-1] and (macd_line >= signal_line).iloc[-2]
    )
    hist_abs = hist.abs()
    trailing_max = hist_abs.rolling(energy_window).max()
    denom = trailing_max.iloc[-1]
    energy = float(hist_abs.iloc[-1] / denom) if pd.notna(denom) and denom > 0 else 0.0
    return {
        "cross_up": cross_up,
        "cross_dn": cross_dn,
        "energy": energy,
        "small_energy": bool(energy < small_energy_thresh),
        "above_zero": bool(macd_line.iloc[-1] > 0),
    }


# ---- stop / risk configuration ----
# The stop is the ONLY real risk control in the engine: R:R is derived from it
# (target = entry + 3 * risk), so a wide stop silently turns "3:1" into a
# trade that needs a 20%+ move. These knobs bound that.
#   max_risk_pct   - a signal whose nearest valid support is further below price
#                    than this is downgraded to WATCH instead of BUY.
#   fallback_pct   - distance of the synthetic stop used when NO support edge fired.
#   stop_buffer    - place the stop just *under* the support level (0.985 = 1.5% under).
DEFAULT_RISK_CFG = {
    "max_risk_pct": 0.08,
    "fallback_pct": 0.03,
    "stop_buffer": 0.985,
}


def _pick_stop(price: float, flip: dict, ma_zone: dict, tl: dict,
               fallback_pct: float, zone: dict | None = None) -> tuple[str, float]:
    """Choose the stop level: the NEAREST support that an edge actually confirmed.

    Rules (both matter):
      * only detectors that FIRED contribute a level — an unfired `level` is just
        a number computed on the way to a False verdict, and a level ABOVE price is
        resistance, not support.
      * of the surviving levels take the HIGHEST one below price (nearest support),
        which yields the tightest stop. Taking the minimum instead — the old
        behaviour — always produced the widest possible stop and let non-fired
        detectors drag it far below the structure.
    """
    cands: list[tuple[str, float]] = []
    if flip.get("flip") and flip.get("level") and flip["level"] < price:
        cands.append(("S/R flip", float(flip["level"])))
    if ma_zone.get("zone") and ma_zone.get("support") and ma_zone["support"] < price:
        cands.append(("MA support", float(ma_zone["support"])))
    if tl.get("bounce") and tl.get("utl") and tl["utl"] < price:
        cands.append(("up-trend line", float(tl["utl"])))
    # meta_edges: S/R zone + ATR-based stop (tightest, structure-aware)
    if zone and zone.get("active") and zone.get("suggested_stop") \
            and zone["suggested_stop"] < price:
        cands.append(("S/R zone", float(zone["suggested_stop"])))
    if not cands:
        return "fallback", price * (1.0 - fallback_pct)
    return max(cands, key=lambda kv: kv[1])


def analyze(
    symbol: str,
    df: pd.DataFrame,
    idx: pd.DataFrame | None = None,
    min_edges: int = 3,
    macd_cfg: dict | None = None,
    st_cfg: dict | None = None,
    risk_cfg: dict | None = None,
) -> dict:
    """Run the full J Law analysis on one symbol. Returns a structured result."""
    df = _add_mas(df)
    zones = me.build_zones(df)  # S/R zones (clustered, from meta_edges)
    ts = trend_structure(df)
    stf = strong_trend_features(df, st_cfg)
    st = stf["score"]
    rs = relative_strength(df, idx)
    flip = _recent_support_flip(df)
    ma_zone = _ma_support_zone(df)
    tl = _channel_or_tl(df)
    weak = _overbought_or_distribution(df)
    base = pat.detect_base_pattern(df)  # cup-with-handle / VCP edge

    # ---- meta_edges: richer, multi-timeframe edge set (ported from meta-screener) ----
    vol_fuel = me.edge_volume_fuel(df)
    lvp_meta = me.edge_low_vol_pullback(df)   # richer low-vol pullback
    near = me.edge_near_support(df, zones)    # S/R zone + ATR stop
    htf = me.edge_weekly_htf(df)              # weekly HTF alignment
    br = me.edge_breakout_retest(df, zones)   # volume breakout + retest
    rs_lead = me.edge_rs_leadership(df, idx)  # 照妖鏡 RS (3 windows)
    div = me.edge_divergence(df)              # RSI/MACD bullish divergence

    price = float(df["close"].iloc[-1])

    edges = []        # bullish edges fired
    bear_edges = []   # bearish edges fired

    # ---- MACD: 能量柱大小過濾 + EMA200 趨勢過濾 (YouTube MACD 策略) ----
    # trend_ok_short defaults to the existing behaviour (not an uptrend); when the
    # EMA200 filter is enabled it is tightened to require price below the 200MA so
    # we never short "against the 200MA" (price still above it = stay long-only).
    ma200 = df["ma200"].iloc[-1] if "ma200" in df.columns else None
    ema200_downtrend = pd.notna(ma200) and price < ma200
    ema200_filter = False
    trend_ok_short = not ts["uptrend"]
    macd = {"enabled": False}
    if macd_cfg is not None and str(macd_cfg.get("enabled", "true")).lower() != "false":
        fast = int(macd_cfg.get("fast", 12))
        slow = int(macd_cfg.get("slow", 26))
        sig = int(macd_cfg.get("signal", 9))
        ewin = int(macd_cfg.get("energy_window", 60))
        small_thr = float(macd_cfg.get("small_energy_thresh", 0.30))
        ema200_filter = str(macd_cfg.get("ema200_filter", "true")).lower() != "false"
        macd = macd_state(df, fast, slow, sig, ewin, small_thr)
        macd["enabled"] = True
        macd["ema200_filter"] = ema200_filter
        if ema200_filter:
            trend_ok_short = ema200_downtrend
        # Bullish MACD edge: 金叉 + 弱動能(小能量柱) + 多頭趨勢 → 高勝率做多
        if macd["cross_up"] and macd["small_energy"] and ts["uptrend"]:
            edges.append(
                f"MACD: 金叉 + 能量柱縮小(弱動能→反轉可信, energy={macd['energy']:.2f}) → 多頭確認"
            )
        # Bearish MACD edge: 死叉 + 弱動能(小能量柱) + 空頭趨勢(EMA200 下) → 高勝率做空
        if macd["cross_dn"] and macd["small_energy"] and trend_ok_short:
            bear_edges.append(
                f"MACD: 死叉 + 能量柱縮小(弱動能→反轉可信, energy={macd['energy']:.2f}) → 空頭確認"
            )

    if ts["uptrend"]:
        edges.append("Trend: HH/HL uptrend, above 50/200MA")
    if stf["is_strong"]:
        _feat_labels = {
            "f1_trend_structure": "HH/HL 上升結構",
            "f2_big_candles_gaps": "大陽/大陰燭+裂口",
            "f3_volume_signature": "升日大成交/跌日低量",
            "f4_follow_through": "follow-through 延續",
            "f5_ma_hugging": "貼 10/20MA",
            "f6_time_efficiency": "短線高時間效益",
        }
        _fired = [
            lbl
            for key, lbl in _feat_labels.items()
            if (stf[key] if isinstance(stf[key], bool) else stf[key] >= 0.5)
        ]
        edges.append(
            f"強勁趨勢 ({stf['count']}/6 特徵, score={st:.2f}): " + "、".join(_fired)
        )
    if rs > 0:
        edges.append(f"Relative strength +{rs:.1f}% vs index (leadership)")
    if flip["flip"]:
        edges.append(f"S/R flip: broke {flip['level']:.2f} resistance, now support")
    if ma_zone["zone"]:
        edges.append(f"MA cluster support ~{ma_zone['support']:.2f}")
    if tl["bounce"]:
        edges.append(f"On up-trend line (UTL) ~{tl['utl']:.2f}")
    # ---- meta_edges: richer, multi-timeframe edges ----
    if vol_fuel["active"]:
        edges.append(
            f"Volume fuel: up-day vol {vol_fuel['up_down_vol_ratio']}x down-day vol"
        )
    if lvp_meta["active"]:
        edges.append("Low-volume pullback (healthy shakeout, <62% leg retrace)")
    if near["active"]:
        edges.append(
            f"Near S/R zone support ~{near['zone_high']:.2f} "
            f"(ATR stop risk {near['risk_pct'] * 100:.1f}%)"
        )
    if htf["active"]:
        edges.append("Weekly HTF aligned (higher-timeframe uptrend)")
    if br["breakout"]["active"]:
        edges.append(
            f"Volume breakout above zone {br['breakout']['zone_high']:.2f} "
            f"(x{br['breakout']['vol_mult']} vol)"
        )
    if br["retest"]["active"]:
        edges.append(
            f"Retest of flipped zone {br['retest']['zone_high']:.2f} holding as support"
        )
    if rs_lead.get("active"):
        tag = ("照妖鏡: 大盤跌佢升" if rs_lead.get("down_market_up_stock")
               else f"RS leadership ({rs_lead.get('beats')}/3 windows)")
        edges.append(tag)
    if div["active"]:
        edges.append(f"Momentum divergence (底背離): {div['detail']}")
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
    risk_pct = None
    stop_label = None
    explanation = []

    if score >= min_edges and ts["uptrend"]:
        rc = {**DEFAULT_RISK_CFG, **(risk_cfg or {})}
        max_risk = float(rc["max_risk_pct"])
        label, support = _pick_stop(
            price, flip, ma_zone, tl, float(rc["fallback_pct"]), near
        )

        entry = round(price, 4)
        stop = round(support * float(rc["stop_buffer"]), 4)
        risk = entry - stop
        risk_pct = (risk / entry) if entry else 0.0
        target = round(entry + 3 * risk, 4)  # R:R 3:1 (J Law baseline)
        rr = round((target - entry) / risk, 2) if risk > 0 else None

        stop_label = label
        if risk > 0 and risk_pct <= max_risk:
            signal = "BUY"
            explanation = edges + [
                f"Entry ~{entry:.2f} | Stop ~{stop:.2f} ({risk_pct * 100:.1f}% risk, "
                f"just under {label}) | Target ~{target:.2f} (R:R {rr}:1)",
                "M.E.T.A. = multiple edges stacked at one price → only take the trade here.",
            ]
        else:
            # Confluence is there, but the structure is too loose to risk-manage:
            # the nearest real support is further away than we are willing to risk,
            # so there is no tight entry. Watch, don't trade.
            signal = "WATCH"
            explanation = edges + [
                f"No tight entry: nearest support ({label}) is {risk_pct * 100:.1f}% below "
                f"price, over the {max_risk * 100:.0f}% risk cap → stop would be too wide.",
                "Watchlist only — wait for price to tighten up near support before entering.",
            ]
    elif len(bear_edges) >= 2 and trend_ok_short:
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
        "strong_trend_features": stf,
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "risk_pct": round(risk_pct * 100, 2) if risk_pct is not None else None,
        "stop_source": stop_label,
        "explanation": explanation,
        "base": base,
        "meta_edges": {
            "volume_fuel": vol_fuel,
            "low_vol_pullback": lvp_meta,
            "near_support": near,
            "weekly_htf": htf,
            "breakout_retest": br,
            "rs_leadership": rs_lead,
            "divergence": div,
            "zones": [{"low": z["low"], "high": z["high"], "touches": z["touches"]}
                      for z in zones],
        },
        "macd": {
            "enabled": macd["enabled"],
            "cross_up": macd.get("cross_up"),
            "cross_dn": macd.get("cross_dn"),
            "energy": round(macd.get("energy", 0.0), 3),
            "small_energy": macd.get("small_energy"),
            "above_zero": macd.get("above_zero"),
            "ema200_filter": ema200_filter,
        },
        "last_date": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else None,
    }
