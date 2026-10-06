"""
bookkeeping.py — Phase 3: personal holdings tracker + price alerts.

A holding is a stock you actually bought. We store it in portfolio.json
(project root) and, when you log a buy, we AUTO-FILL the target (and stop)
from the scanner's own projection in the latest scan_result.json — that is
the "past knowledge" of the chart (its measured-move / 3:1 reward:risk plan).
If the symbol isn't in the last scan, we fall back to buy_price * 1.20.

Live price + P/L are read from the latest scan_result.json (same source the
email and web dashboard use), so everything stays consistent without a
separate data fetch.

IMPORTANT: scan_result.json only contains that day's BUY/SELL picks, so a
holding that is not currently a pick has NO row there. get_current_price()
therefore falls back to a direct quote from the data provider (disk-cached,
so it is cheap). Without that fallback the price silently collapsed to the
buy price, freezing P/L at 0% and meaning alerts never fired for most
holdings.

CLI (handy for quick adds / testing):
    python -m src.bookkeeping add AAPL us 180.5 100
    python -m src.bookkeeping add 2359.HK hk 197.1 500 --target 223.4 --stop 188.3
    python -m src.bookkeeping list
    python -m src.bookkeeping remove <id>
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, date

from src import data_provider as dp

log = logging.getLogger("jlaw.book")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTFOLIO_FILE = os.path.join(ROOT, "portfolio.json")
SCAN_FILE = os.path.join(ROOT, "scan_result.json")

# If a symbol isn't in the last scan, target = buy_price * this.
DEFAULT_TARGET_MULT = 1.20
# Within this fraction of target (price >= target * (1 - NEAR)) => APPROACHING.
NEAR_FRACTION = 0.05


# --------------------------------------------------------------------------
# storage helpers
# --------------------------------------------------------------------------
def _load() -> dict:
    if not os.path.exists(PORTFOLIO_FILE):
        return {"holdings": []}
    try:
        with open(PORTFOLIO_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "holdings" not in data:
            data = {"holdings": []}
    except Exception:
        data = {"holdings": []}
    return data


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
    tmp = PORTFOLIO_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, PORTFOLIO_FILE)


def _norm_symbol(symbol: str, market: str) -> str:
    symbol = symbol.strip().upper()
    if market == "hk" and not symbol.endswith(".HK"):
        symbol = symbol + ".HK"
    return symbol


# --------------------------------------------------------------------------
# scan_result.json lookups (the "past knowledge")
# --------------------------------------------------------------------------
def _scan_records() -> list[dict]:
    if not os.path.exists(SCAN_FILE):
        return []
    try:
        with open(SCAN_FILE, encoding="utf-8") as f:
            scan = json.load(f)
    except Exception:
        return []
    recs = []
    for market, data in scan.items():
        if not isinstance(data, dict):
            continue
        for side in ("buys", "sells"):
            for r in data.get(side, []) or []:
                recs.append(r)
    return recs


def _find_scan_row(symbol: str, market: str) -> dict | None:
    symbol = _norm_symbol(symbol, market)
    for r in _scan_records():
        if r.get("symbol") == symbol and r.get("market") == market:
            return r
    # fall back: match on symbol only (market may differ in stored record)
    for r in _scan_records():
        if r.get("symbol") == symbol:
            return r
    return None


def get_scan_target(symbol: str, market: str):
    """Return (target, stop) from the latest scan, or (None, None)."""
    row = _find_scan_row(symbol, market)
    if not row:
        return None, None
    t = row.get("target")
    s = row.get("stop")
    try:
        t = float(t) if t is not None else None
    except (TypeError, ValueError):
        t = None
    try:
        s = float(s) if s is not None else None
    except (TypeError, ValueError):
        s = None
    return t, s


# Per-process memo for provider quotes, so `list` over a large portfolio only
# fetches each symbol once. dp.fetch() itself is disk-cached (20h) on top.
_QUOTE_CACHE: dict[tuple[str, str], float | None] = {}
_dp_configured = False


def _configure_provider() -> None:
    """Apply config.ini [data] to data_provider.

    main.load_config() normally does this, but the CLI entry point
    (`python -m src.bookkeeping ...`) never goes through it, so the per-market
    source policy would otherwise sit at module defaults.
    """
    global _dp_configured
    if _dp_configured:
        return
    _dp_configured = True
    try:
        import configparser

        cp = configparser.ConfigParser()
        cp.read(os.path.join(ROOT, "config.ini"))
        if cp.has_section("data"):
            dp.configure(dict(cp["data"]))
    except Exception:  # noqa: BLE001
        log.debug("could not apply [data] config", exc_info=True)


def fetch_last_close(symbol: str, market: str):
    """Last close straight from the data provider, or None if it fails."""
    key = (_norm_symbol(symbol, market), market)
    if key in _QUOTE_CACHE:
        return _QUOTE_CACHE[key]
    price = None
    try:
        _configure_provider()
        df = dp.fetch(key[0], period="1mo", interval="1d",
                      use_cache=True, max_age_hours=20.0)
        if df is not None and len(df):
            price = float(df["close"].iloc[-1])
    except Exception:  # noqa: BLE001
        log.warning("live quote failed for %s", key[0], exc_info=True)
    _QUOTE_CACHE[key] = price
    return price


def get_current_price(symbol: str, market: str):
    """Prefer the scan's own price; fall back to a real quote.

    The fallback matters: scan_result.json only holds the current BUY/SELL
    picks, so without it every other holding reported its own buy price.
    """
    row = _find_scan_row(symbol, market)
    if row:
        try:
            return float(row.get("price"))
        except (TypeError, ValueError):
            pass
    return fetch_last_close(symbol, market)


# --------------------------------------------------------------------------
# holdings CRUD
# --------------------------------------------------------------------------
def load_holdings() -> list[dict]:
    return _load().get("holdings", [])


def add_holding(
    symbol: str,
    market: str,
    buy_price: float,
    qty: float,
    buy_date: str | None = None,
    target: float | None = None,
    stop: float | None = None,
    note: str = "",
) -> dict:
    market = market.strip().lower()
    symbol = _norm_symbol(symbol, market)
    buy_price = float(buy_price)
    qty = float(qty)

    # Auto-fill target/stop from the scanner's projection ("past knowledge").
    auto_target = target is None
    auto_stop = stop is None
    if auto_target or auto_stop:
        st, ss = get_scan_target(symbol, market)
        if auto_target and st is not None:
            target = st
        if auto_stop and ss is not None:
            stop = ss
    if target is None:
        target = round(buy_price * DEFAULT_TARGET_MULT, 4)
    if stop is None:
        stop = None

    hid = uuid.uuid4().hex[:12]
    holding = {
        "id": hid,
        "symbol": symbol,
        "market": market,
        "buy_date": buy_date or date.today().isoformat(),
        "buy_price": buy_price,
        "qty": qty,
        "target": target,
        "stop": stop,
        "note": note or "",
        "auto_target": auto_target,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    data = _load()
    data.setdefault("holdings", []).append(holding)
    _save(data)
    return holding


def update_holding(hid: str, **fields) -> bool:
    data = _load()
    for h in data.get("holdings", []):
        if h.get("id") == hid:
            for k, v in fields.items():
                if k in ("symbol", "market", "buy_date", "note"):
                    h[k] = v
                elif k in ("buy_price", "qty", "target", "stop"):
                    h[k] = None if v in (None, "") else float(v)
            # if user manually sets target, it's no longer auto
            if "target" in fields and fields["target"] not in (None, ""):
                h["auto_target"] = False
            _save(data)
            return True
    return False


def remove_holding(hid: str) -> bool:
    data = _load()
    before = len(data.get("holdings", []))
    data["holdings"] = [h for h in data.get("holdings", []) if h.get("id") != hid]
    if len(data["holdings"]) == before:
        return False
    _save(data)
    return True


# --------------------------------------------------------------------------
# live status + alerts
# --------------------------------------------------------------------------
def compute_status(h: dict) -> dict:
    """Return the holding enriched with live price, P/L and alert state."""
    symbol = h["symbol"]
    market = h["market"]
    buy_price = float(h["buy_price"])
    qty = float(h["qty"])
    price = get_current_price(symbol, market)
    live = price is not None
    if price is None:
        price = buy_price  # no live quote -> neutral

    cost = buy_price * qty
    value = price * qty
    pnl = value - cost
    pnl_pct = (pnl / cost * 100.0) if cost else 0.0

    target = h.get("target")
    stop = h.get("stop")
    dist_to_target_pct = None
    if target:
        try:
            dist_to_target_pct = (float(target) - price) / price * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            dist_to_target_pct = None

    alert = None
    if target and price >= float(target):
        alert = "TARGET HIT"
    elif target and price >= float(target) * (1 - NEAR_FRACTION):
        alert = "APPROACHING"
    elif stop and price <= float(stop):
        alert = "STOP HIT"

    return {
        **h,
        "price": price,
        "live": live,
        "cost": cost,
        "value": value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "dist_to_target_pct": dist_to_target_pct,
        "alert": alert,
    }


def compute_alerts() -> list[dict]:
    """Holdings whose price has hit / is approaching target, or hit stop."""
    out = []
    for h in load_holdings():
        st = compute_status(h)
        if st.get("alert"):
            out.append(st)
    # order: TARGET HIT and STOP HIT first, then APPROACHING
    rank = {"TARGET HIT": 0, "STOP HIT": 1, "APPROACHING": 2}
    out.sort(key=lambda x: rank.get(x["alert"], 9))
    return out


# --------------------------------------------------------------------------
# email rendering
# --------------------------------------------------------------------------
def build_alerts_html(alerts: list[dict]) -> str:
    if not alerts:
        return ""
    rows = []
    for a in alerts:
        price = f"{a['price']:.2f}"
        tgt = f"{float(a['target']):.2f}" if a.get("target") else "-"
        pnl = f"{a['pnl_pct']:+.1f}%"
        color = "#c0392b" if a["alert"] in ("TARGET HIT", "STOP HIT") else "#b9770e"
        rows.append(
            f"<tr>"
            f"<td><b>{a['symbol']}</b></td>"
            f"<td>{a['market'].upper()}</td>"
            f"<td>{float(a['buy_price']):.2f}</td>"
            f"<td>{price}</td>"
            f"<td>{tgt}</td>"
            f"<td>{pnl}</td>"
            f"<td style='color:{color};font-weight:bold'>{a['alert']}</td>"
            f"</tr>"
        )
    return (
        "<div style='margin-top:24px;border:2px solid #e74c3c;border-radius:8px;"
        "padding:12px 16px;background:#fff5f5;'>"
        "<h2 style='margin:0 0 8px;color:#c0392b;'>Price Alerts (your holdings)</h2>"
        "<table style='border-collapse:collapse;width:100%;font-size:13px;'>"
        "<tr style='text-align:left;color:#555;'>"
        "<th>Symbol</th><th>Mkt</th><th>Buy</th><th>Now</th>"
        "<th>Target</th><th>P/L</th><th>Status</th></tr>"
        + "".join(rows)
        + "</table></div>"
    )


def build_alerts_text(alerts: list[dict]) -> str:
    if not alerts:
        return ""
    lines = ["", "PRICE ALERTS (your holdings)", "----------------------------"]
    for a in alerts:
        tgt = f"{float(a['target']):.2f}" if a.get("target") else "-"
        lines.append(
            f"{a['symbol']} ({a['market']})  buy {float(a['buy_price']):.2f}  "
            f"now {a['price']:.2f}  target {tgt}  "
            f"P/L {a['pnl_pct']:+.1f}%  -> {a['alert']}"
        )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# tiny CLI
# --------------------------------------------------------------------------
def _cli():
    import argparse

    p = argparse.ArgumentParser(description="JLaw bookkeeping (holdings)")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("add", help="log a bought stock")
    a.add_argument("symbol")
    a.add_argument("market", choices=["hk", "us"])
    a.add_argument("buy_price", type=float)
    a.add_argument("qty", type=float)
    a.add_argument("--buy-date")
    a.add_argument("--target", type=float)
    a.add_argument("--stop", type=float)
    a.add_argument("--note", default="")

    sub.add_parser("list")

    r = sub.add_parser("remove")
    r.add_argument("id")

    args = p.parse_args()
    if args.cmd == "add":
        h = add_holding(
            args.symbol, args.market, args.buy_price, args.qty,
            buy_date=args.buy_date, target=args.target,
            stop=args.stop, note=args.note,
        )
        print("ADDED:", json.dumps(h, ensure_ascii=False))
    elif args.cmd == "list":
        for h in load_holdings():
            st = compute_status(h)
            print(
                f"{h['id']}  {h['symbol']:>10} {h['market']:>3}  "
                f"buy {h['buy_price']:.2f} x{h['qty']:.0f}  "
                f"now {st['price']:.2f}  P/L {st['pnl_pct']:+.1f}%  "
                f"target {h.get('target')}  [{st['alert'] or 'ok'}]"
            )
        if not load_holdings():
            print("(no holdings yet)")
    elif args.cmd == "remove":
        print("REMOVED" if remove_holding(args.id) else "NOT FOUND")
    else:
        p.print_help()


if __name__ == "__main__":
    _cli()
