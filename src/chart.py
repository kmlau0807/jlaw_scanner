"""
chart.py — self-contained TA chart for the J Law scanner detail view.

Generates a matplotlib PNG (base64) from cached yfinance data so the detail
page always has a chart, independent of TradingView's free embed data feed.
Overlays the J Law levels (entry / stop / target) and the 10/20/50/200 MAs.
"""
from __future__ import annotations

import io
import base64
import pandas as pd

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

from . import data_provider as dp

_MA_SPECS = [("MA10", 10), ("MA20", 20), ("MA50", 50), ("MA200", 200)]
_MA_COLORS = ["#f5a623", "#ffd166", "#06d6a0", "#ef476f"]
_BG = "#0e1726"
_GRID = "#22304a"
_TEXT = "#9fb3c8"
_PRICE = "#4da3ff"


def build_chart(symbol: str, market: str, rec: dict | None = None,
                period: str = "200d") -> str | None:
    """Return a base64 PNG data URI, or None if data is unavailable."""
    df = dp.fetch(symbol, period=period, interval="1d")
    if df is None or len(df) < 20:
        return None

    df = df.tail(160)
    mas = {name: df["close"].rolling(n).mean() for name, n in _MA_SPECS}

    rc = {
        "figure.facecolor": _BG,
        "axes.facecolor": _BG,
        "text.color": "#ffffff",
        "axes.labelcolor": "#ffffff",
        "xtick.color": _TEXT,
        "ytick.color": _TEXT,
        "legend.labelcolor": "#ffffff",
    }
    with plt.rc_context(rc):
        fig = plt.figure(figsize=(10, 5.2), dpi=110)
        gs = GridSpec(2, 1, height_ratios=[3.2, 1], hspace=0.08)
        ax = fig.add_subplot(gs[0])
        ax2 = fig.add_subplot(gs[1], sharex=ax)

        ax.plot(df.index, df["close"], color=_PRICE, lw=1.3, label="Close")
        for (name, _n), c in zip(_MA_SPECS, _MA_COLORS):
            ax.plot(df.index, mas[name], lw=1.0, label=name, color=c)

        if rec:
            for key, col in (("entry", "#06d6a0"), ("stop", "#ef476f"),
                             ("target", "#4da3ff")):
                v = rec.get(key)
                if isinstance(v, (int, float)) and v > 0:
                    ax.axhline(v, color=col, ls="--", lw=1.1,
                               label=key.capitalize())

        # base-pattern annotation: dashed rim / breakout line
        base = rec.get("base") if rec else None
        if base and base.get("found") and isinstance(base.get("rim"), (int, float)):
            ax.axhline(base["rim"], color="#e0b341", ls=":", lw=1.2,
                       label="Base rim / pivot")
            bx = df.index[len(df) // 2]
            ax.text(bx, base["rim"], " rim", color="#e0b341",
                    fontsize=7, va="bottom")

        ax.set_ylabel("Price")
        ax.legend(loc="upper left", fontsize=7, ncol=3, framealpha=0.6)
        ax.grid(alpha=0.15)
        ax2.bar(df.index, df["volume"], color="#3a4a63", width=1.0)
        ax2.set_ylabel("Vol")
        ax2.tick_params(labelsize=7)
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(0)
            lbl.set_fontsize(7)

        title = f"{symbol}  -  J Law M.E.T.A. view"
        if rec and rec.get("last_date"):
            title += f"   (as of {rec['last_date']})"
        fig.suptitle(title, fontsize=11, color="#ffffff")

        for a in (ax, ax2):
            for sp in a.spines.values():
                sp.set_color(_GRID)
            a.tick_params(colors=_TEXT)
            a.yaxis.label.set_color(_TEXT)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)

    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def tradingview_url(symbol: str, market: str) -> str:
    """Link that opens the chart on tradingview.com (user logs in there)."""
    if market == "hk":
        code = symbol.replace(".HK", "")
        sym = f"HKEX:{code}"
    else:
        sym = symbol
    return f"https://www.tradingview.com/chart/?symbol={sym}"
