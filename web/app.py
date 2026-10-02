"""
web/app.py — Phase 2: manual web interface for the J Law scanner.

Run:
    cd jlaw_scanner
    python web/app.py
Then open http://localhost:5000 (or http://<this-PC-LAN-IP>:5000 from
another machine on the same network — see the [web] section in config.ini).

Reuses the exact same engine as Phase 1 (src.scanner / src.indicators),
so the web view and the email report never disagree.
"""
from __future__ import annotations
import os
import sys
import json
import threading
import configparser
from flask import Flask, render_template, request, redirect, url_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import scanner as scn  # noqa: E402
from src import chart as chartmod  # noqa: E402
from src import bookkeeping as bk  # noqa: E402

app = Flask(__name__, template_folder="templates", static_folder="static")
RESULT_FILE = os.path.join(ROOT, "scan_result.json")
CONFIG_FILE = os.path.join(ROOT, "config.ini")

_scanning = False
_result = None
_lock = threading.Lock()


def load_config() -> dict:
    cp = configparser.ConfigParser()
    cp.read(CONFIG_FILE)
    cfg = {}
    for sec in ("scan", "schedule", "paths"):
        if cp.has_section(sec):
            cfg.update(dict(cp[sec]))
    return cfg


def tradingview_symbol(symbol: str, market: str) -> str:
    if market == "hk":
        code = symbol.replace(".HK", "")
        return f"HKEX:{code}"
    return symbol  # US tickers resolve directly on TradingView


def _run_scan():
    global _scanning, _result
    cfg = load_config()
    markets = [m.strip() for m in cfg.get("markets", "hk,us").split(",") if m.strip()]
    rs_map = {"us": cfg.get("rs_index_us"), "hk": cfg.get("rs_index_hk")}
    scan = scn.scan_all(
        markets,
        top_n=int(cfg.get("top_n", 10)),
        min_edges=int(cfg.get("min_metascore", 3)),
        lookback_days=int(cfg.get("lookback_days", 180)),
        min_avg_dollar_volume=float(cfg.get("min_avg_dollar_volume", 5_000_000)),
        rs_map=rs_map,
    )
    with _lock:
        _result = scan
        try:
            with open(RESULT_FILE, "w", encoding="utf-8") as f:
                json.dump(scan, f, ensure_ascii=False, indent=2, default=str)
        except Exception:
            pass
    _scanning = False


@app.route("/", methods=["GET"])
def index():
    global _result
    with _lock:
        result = _result
    if result is None and os.path.exists(RESULT_FILE):
        try:
            with open(RESULT_FILE, encoding="utf-8") as f:
                result = json.load(f)
        except Exception:
            result = None
    return render_template(
        "index.html",
        scan=result,
        scanning=_scanning,
        tv=tradingview_symbol,
    )


@app.route("/run", methods=["POST"])
def run_scan():
    global _scanning
    if not _scanning:
        _scanning = True
        threading.Thread(target=_run_scan, daemon=True).start()
    return redirect(url_for("index"))


@app.route("/detail/<market>/<symbol>")
def detail(market, symbol):
    sym = symbol
    result = _result
    if result is None and os.path.exists(RESULT_FILE):
        try:
            with open(RESULT_FILE, encoding="utf-8") as f:
                result = json.load(f)
        except Exception:
            result = None
    rec = None
    side = None
    if result and market in result:
        for bucket, key in (("buys", "BUY"), ("sells", "SELL")):
            for r in result[market].get(bucket, []):
                if r["symbol"] == sym:
                    rec = r
                    side = key
                    break
    chart_b64 = chartmod.build_chart(sym, market, rec) if rec else None
    return render_template(
        "detail.html",
        rec=rec,
        market=market,
        side=side,
        tv_symbol=tradingview_symbol(sym, market),
        tv_url=chartmod.tradingview_url(sym, market),
        chart_b64=chart_b64,
    )


@app.route("/portfolio", methods=["GET"])
def portfolio():
    """Phase 3: personal holdings tracker + live P/L + price alerts."""
    holdings = [bk.compute_status(h) for h in bk.load_holdings()]
    alerts = bk.compute_alerts()
    return render_template(
        "portfolio.html",
        holdings=holdings,
        alerts=alerts,
        tv=tradingview_symbol,
    )


@app.route("/portfolio/add", methods=["POST"])
def portfolio_add():
    sym = request.form.get("symbol", "").strip()
    market = request.form.get("market", "us").strip().lower()
    try:
        buy_price = float(request.form.get("buy_price", ""))
        qty = float(request.form.get("qty", ""))
    except (TypeError, ValueError):
        return redirect(url_for("portfolio"))
    target = request.form.get("target") or None
    stop = request.form.get("stop") or None
    target = float(target) if target else None
    stop = float(stop) if stop else None
    bk.add_holding(
        sym, market, buy_price, qty,
        buy_date=request.form.get("buy_date") or None,
        target=target,
        stop=stop,
        note=request.form.get("note", ""),
    )
    return redirect(url_for("portfolio"))


@app.route("/portfolio/remove/<hid>", methods=["POST"])
def portfolio_remove(hid):
    bk.remove_holding(hid)
    return redirect(url_for("portfolio"))


if __name__ == "__main__":
    # Bind to 0.0.0.0 so other machines on the LAN can open the dashboard.
    # Override with [web] host/port in config.ini. Use your LAN IP
    # (e.g. 192.168.254.11) to limit exposure to a single interface.
    _cfg = configparser.ConfigParser()
    _cfg.read(CONFIG_FILE)
    _host = _cfg.get("web", "host", fallback="0.0.0.0")
    _port = _cfg.getint("web", "port", fallback=5000)
    app.run(host=_host, port=_port, debug=False, threaded=True)
