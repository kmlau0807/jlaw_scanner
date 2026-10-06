"""Audit the R:R / stop-placement rule over the cached HK price data.

Runs the real indicators.analyze() on every cached HK symbol and reports:
  * whether `rr` is ever anything other than 3.0 (it is derived, not enforced)
  * BUY vs WATCH counts under the current [risk] config
  * the risk% distribution BEFORE (old min()-of-everything stop) and AFTER
    (nearest confirmed support + risk cap), side by side
"""
from __future__ import annotations

import glob
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from src import indicators as ind  # noqa: E402

RISK_CFG = {"max_risk_pct": 0.08, "fallback_pct": 0.03, "stop_buffer": 0.985}
MACD_CFG = {
    "enabled": "true", "fast": 12, "slow": 26, "signal": 9,
    "energy_window": 60, "small_energy_thresh": 0.30, "ema200_filter": "true",
}
ST_CFG = {
    "win": 30, "struct_win": 60, "body_thresh": 0.02, "gap_thresh": 0.01,
    "vol_ratio_thresh": 1.0, "time_eff_win": 20, "time_eff_ret": 0.20,
    "min_features": 5, "score_thresh": 0.6,
}


def old_stop(price, flip, zone, tl):
    """The pre-fix behaviour, reproduced for comparison."""
    support = min([x for x in (flip["level"], zone["support"], tl["utl"]) if x]
                  + [price * 0.97])
    return support * 0.985


def load(path: str):
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df.sort_index()


def main() -> None:
    files = sorted(glob.glob(os.path.join(HERE, "cache", "*_HK_1d.csv")))
    print(f"{len(files)} cached HK symbols\n")

    rows, rrs = [], []
    for path in files:
        sym = os.path.basename(path).replace("_HK_1d.csv", "")
        try:
            df = load(path)
            if len(df) < 60:
                continue
            r = ind.analyze(sym + ".HK", df, idx=None, min_edges=3,
                            macd_cfg=MACD_CFG, st_cfg=ST_CFG, risk_cfg=RISK_CFG)
        except Exception as e:  # noqa: BLE001
            print(f"  {sym}: ERROR {e}")
            continue
        if r["signal"] not in ("BUY", "WATCH") or not r["entry"] or not r["stop"]:
            continue

        d = ind._add_mas(df.copy())
        price = float(d["close"].iloc[-1])
        ostop = old_stop(price, ind._recent_support_flip(d),
                         ind._ma_support_zone(d), ind._channel_or_tl(d))
        orisk = (price - ostop) / price * 100

        rrs.append(r["rr"])
        rows.append((sym, r["signal"], price, r["stop"], r["risk_pct"],
                     orisk, r.get("stop_source")))

    n_buy = sum(1 for r in rows if r[1] == "BUY")
    n_watch = sum(1 for r in rows if r[1] == "WATCH")
    print(f"confluence hits: {len(rows)}  ->  BUY {n_buy} / WATCH {n_watch}")
    print(f"distinct rr values: {sorted(set(rrs))}\n")

    new = pd.Series([r[4] for r in rows])
    old = pd.Series([r[5] for r in rows])
    print("risk% (entry -> stop distance)")
    print(f"{'':>8}{'before':>10}{'after':>10}")
    for name, s in (("min", new.min()), ("median", new.median()), ("max", new.max())):
        pass
    print(f"{'min':>8}{old.min():>10.2f}{new.min():>10.2f}")
    print(f"{'median':>8}{old.median():>10.2f}{new.median():>10.2f}")
    print(f"{'max':>8}{old.max():>10.2f}{new.max():>10.2f}")
    print(f"{'mean':>8}{old.mean():>10.2f}{new.mean():>10.2f}")
    print(f"\n  over the 8% cap  ->  before: {(old > 8).sum()}   after: {(new > 8).sum()}")
    print(f"  implied target move:  before {3 * old.min():.1f}%-{3 * old.max():.1f}%"
          f"   after {3 * new.min():.1f}%-{3 * new.max():.1f}%")

    print(f"\n{'sym':>6} {'signal':>7} {'price':>9} {'new stop':>10} "
          f"{'risk%':>7} {'was%':>7}  source")
    for sym, sig, p, st, rp, orp, src in sorted(rows, key=lambda x: -x[4]):
        print(f"{sym:>6} {sig:>7} {p:>9.2f} {st:>10.2f} {rp:>7.2f} {orp:>7.2f}  {src}")


if __name__ == "__main__":
    main()
