"""
patterns.py — base-pattern detection (cup-with-handle + VCP) as a J Law edge.

Adapts the accuracy-tuned criteria from the user's stock-cah-app detector v3
to operate on the pandas OHLCV frame the scanner already fetches (no new deps).

Why it's a M.E.T.A. edge: a *completed or near-complete* base shows
institutional accumulation + volatility contraction + a pivot to break out of.
It is one independent edge from a different participant group (accumulators),
so it stacks with trend / RS / S/R / MA edges.

We only count it as a *bullish* edge when price is at/near the pivot
(actionable now) or has just broken out on volume.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULTS = {
    # cup-with-handle gates (from cah-app v3)
    "rim_recent_min": 0.45, "rim_gap_max_pct": 18,
    "cup_depth_min_pct": 12, "cup_depth_max_pct": 45,
    "bottom_pos_min": 0.30, "bottom_pos_max": 0.70,
    "handle_depth_min_pct": 3, "handle_depth_max_pct": 22,
    "handle_span_min": 12, "handle_span_max": 45, "cup_span_min": 40,
    "near_rim_min_pct": -3, "near_rim_max_pct": 18,
    "pre_uptrend_adv_min_pct": 8, "u_shape_band_pct": 4, "u_shape_min_days": 3,
    "vol_handle_max_ratio": 0.80, "vol_breakout_min_ratio": 1.20,
    # vcp gates
    "vcp_corrections_min": 2, "vcp_contraction_min_pct": 8,
    # shared: how close to the pivot counts as "actionable now"
    "at_pivot_pct": 3.0,
}


def _slope(closes: np.ndarray) -> float:
    n = len(closes)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=float)
    mx, my = x.mean(), closes.mean()
    den = ((x - mx) ** 2).sum()
    return float(((x - mx) * (closes - my)).sum() / den) if den else 0.0


def _swings(sm: np.ndarray, order: int = 3):
    """Return collapsed list of (index, 'high'/'low', value) pivots."""
    out = []
    for i in range(order, len(sm) - order):
        seg = sm[i - order:i + order + 1]
        if sm[i] >= seg.max():
            out.append((i, "high", sm[i]))
        elif sm[i] <= seg.min():
            out.append((i, "low", sm[i]))
    collapsed = []
    for it in out:
        if collapsed and collapsed[-1][1] == it[1]:
            continue
        collapsed.append(it)
    return collapsed


def _cup_handle(df: pd.DataFrame, c: dict) -> dict | None:
    closes = df["close"].values.astype(float)
    vols = df["volume"].values.astype(float)
    n = len(closes)

    R_idx = int(np.argmax(closes))
    R = float(closes[R_idx])
    R_pos = R_idx / (n - 1)
    left_seg_end = max(0, R_idx - 15)
    if left_seg_end < 20:
        return None
    L_idx = int(np.argmax(closes[:left_seg_end]))
    L = float(closes[L_idx])
    cup_seg = closes[L_idx:R_idx + 1]
    B_off = int(np.argmin(cup_seg))
    B_idx = L_idx + B_off
    B = float(cup_seg[B_off])
    cup_depth = (R - B) / R
    rim_gap = (R - L) / R
    cup_span = R_idx - L_idx
    bottom_pos = B_off / max(1, cup_span)
    after = closes[R_idx:]
    H_off = int(np.argmin(after))
    H_idx = R_idx + H_off
    H = float(after[H_off])
    handle_depth = (R - H) / R
    handle_span = n - R_idx
    cur = float(closes[-1])
    near_rim = (R - cur) / R

    def avg_vol(a: int, b: int) -> float:
        seg = vols[a:b]
        return float(seg.mean()) if len(seg) else 0.0

    vol_handle = avg_vol(R_idx, n)
    vol_cupup = avg_vol(L_idx, R_idx)
    vol_ratio = vol_handle / vol_cupup if vol_cupup else 1.0
    vol_breakout = (avg_vol(max(R_idx, n - 10), n) / vol_handle) if vol_handle else 1.0

    fails = []
    if R_pos < c["rim_recent_min"]:
        fails.append("rim too old")
    if rim_gap * 100 > c["rim_gap_max_pct"]:
        fails.append("rim gap too wide")
    if not (c["cup_depth_min_pct"] <= cup_depth * 100 <= c["cup_depth_max_pct"]):
        fails.append("cup depth out of range")
    if not (c["bottom_pos_min"] <= bottom_pos <= c["bottom_pos_max"]):
        fails.append("bottom off-center")
    if handle_depth * 100 < c["handle_depth_min_pct"]:
        fails.append("no handle dip")
    if handle_depth * 100 > c["handle_depth_max_pct"]:
        fails.append("handle too deep")
    if handle_span < c["handle_span_min"]:
        fails.append("handle too short")
    if handle_span > c["handle_span_max"]:
        fails.append("handle too long")
    if cup_span < c["cup_span_min"]:
        fails.append("cup too short")
    if not (c["near_rim_min_pct"] <= near_rim * 100 <= c["near_rim_max_pct"]):
        fails.append("not near pivot")
    pre = closes[max(0, L_idx - 80):L_idx + 1]
    if len(pre) >= 20:
        adv = (L - pre[0]) / pre[0] * 100
        if _slope(pre) <= 0 or adv < c["pre_uptrend_adv_min_pct"]:
            fails.append("no prior uptrend")
    band = c["u_shape_band_pct"]
    near_bottom = int(np.sum(cup_seg <= B * (1 + band / 100)))
    if near_bottom < c["u_shape_min_days"]:
        fails.append("V-shape not U")
    if handle_depth * 100 >= cup_depth * 100:
        fails.append("handle>=cup")
    if vol_ratio > c["vol_handle_max_ratio"]:
        fails.append("handle vol not contracted")
    breakout = vol_breakout >= c["vol_breakout_min_ratio"]

    score = 100.0
    score -= abs(rim_gap * 100 - 5) * 0.5
    score -= abs(handle_depth * 100 - 9) * 1.0
    score -= abs(cup_depth * 100 - 22) * 0.5
    score += (vol_breakout - 1) * 60
    score -= max(0, near_rim * 100) * 1.0
    score = max(0.0, score)

    return {
        "kind": "cup_with_handle", "ok": not fails, "verdict": "YES" if not fails else "NO",
        "fails": fails, "score": round(score, 1),
        "rim": R, "left_rim": L, "bottom": B, "handle": H, "cur": cur,
        "near_rim_pct": round(near_rim * 100, 2),
        "cup_depth_pct": round(cup_depth * 100, 1),
        "handle_depth_pct": round(handle_depth * 100, 1),
        "vol_ratio": round(vol_ratio, 2), "breakout": bool(breakout),
    }


def _vcp(df: pd.DataFrame, c: dict) -> dict | None:
    sm = pd.Series(df["close"].values).rolling(3, min_periods=1).mean().values.astype(float)
    vols = df["volume"].values.astype(float)
    n = len(sm)

    # pivot high in left ~78% so a base exists to its right
    search_end = max(20, int(n * 0.78))
    R_idx = int(np.argmax(sm[:search_end]))
    R = float(sm[R_idx])

    # find where the prior uptrend into the pivot began (price well below R)
    start = 0
    for i in range(R_idx, 0, -1):
        if sm[i] <= R * 0.85:
            start = i
            break
    seg = sm[start:R_idx + 1]
    if len(seg) < 30:
        return None

    sw = _swings(seg, order=3)
    corrections = []
    cur_high = None
    for _, typ, val in sw:
        if typ == "high":
            cur_high = val
        else:  # low -> a completed correction from the last high
            if cur_high is not None:
                corrections.append((cur_high - val) / cur_high)
                cur_high = None
    if len(corrections) < c["vcp_corrections_min"]:
        return None

    depths = [float(d) for d in corrections]
    contracting = all(depths[i] <= depths[i - 1] * 1.05 for i in range(1, len(depths)))
    total_contract = (depths[0] - depths[-1]) * 100

    span = max(1, R_idx - start)
    vf = vols[start:start + span // 2].mean()
    vl = vols[start + span // 2:R_idx + 1].mean()
    vol_contracted = bool(vl <= vf * 1.05) if vf > 0 else False

    near = (R - float(sm[-1])) / R * 100 <= c["at_pivot_pct"]

    ok = contracting and total_contract >= c["vcp_contraction_min_pct"] and near and vol_contracted
    score = 0.0
    if contracting:
        score += 40
    if total_contract >= c["vcp_contraction_min_pct"]:
        score += 30
    if vol_contracted:
        score += 15
    if near:
        score += 15

    return {
        "kind": "vcp", "ok": ok, "verdict": "YES" if ok else "NO",
        "fails": [] if ok else ["not contracting / no volume dry-up / far from pivot"],
        "score": round(score, 1),
        "rim": R, "bottom": float(seg.min()), "cur": float(sm[-1]),
        "near_rim_pct": round((R - float(sm[-1])) / R * 100, 2),
        "corrections": len(corrections),
        "first_depth_pct": round(depths[0] * 100, 1),
        "last_depth_pct": round(depths[-1] * 100, 1),
        "vol_contracted": vol_contracted, "breakout": False,
    }


def _edge_text(best: dict, at_pivot: bool) -> str:
    if best["kind"] == "cup_with_handle":
        return (
            f"Base: cup-with-handle (cup {best.get('cup_depth_pct',0):.0f}%, "
            f"handle {best.get('handle_depth_pct',0):.0f}%, vol contracted) "
            f"→ pivot {best.get('rim',0):.2f} "
            + ("at handle / near breakout" if at_pivot else "forming")
        )
    return (
        f"Base: VCP ({best.get('corrections',0)} contractions "
        f"{best.get('first_depth_pct',0):.0f}%→{best.get('last_depth_pct',0):.0f}%, "
        f"vol contracted) → tight pivot {best.get('rim',0):.2f} "
        + ("at breakout" if at_pivot else "forming")
    )


def detect_base_pattern(df: pd.DataFrame, cfg: dict | None = None) -> dict:
    """Return a structured base-pattern result for one symbol's OHLCV frame."""
    c = dict(DEFAULTS)
    if cfg:
        c.update({k: v for k, v in cfg.items() if k in DEFAULTS})

    if len(df) < 150:
        return {"found": False, "kind": None, "reason": "insufficient data (<150 bars)"}

    cup = _cup_handle(df, c)
    vcp = _vcp(df, c)
    firing = [x for x in (cup, vcp) if x and x["ok"]]

    if not firing:
        return {
            "found": False, "kind": None,
            "cup": cup, "vcp": vcp,
            "text": "No clean base (cup/VCP) detected.",
        }

    best = max(firing, key=lambda x: x["score"])
    at_pivot = abs(best.get("near_rim_pct", 99)) <= c["at_pivot_pct"]
    return {
        "found": True,
        "kind": best["kind"],
        "at_pivot": at_pivot,
        "verdict": best["verdict"],
        "score": best["score"],
        "rim": best.get("rim"),
        "bottom": best.get("bottom"),
        "handle": best.get("handle"),
        "near_rim_pct": best.get("near_rim_pct"),
        "cup_depth_pct": best.get("cup_depth_pct"),
        "handle_depth_pct": best.get("handle_depth_pct"),
        "breakout": best.get("breakout", False),
        "text": _edge_text(best, at_pivot),
        "raw": best,
    }
