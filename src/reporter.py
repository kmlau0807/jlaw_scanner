"""
reporter.py
-----------
Turns scan results into a human-readable table + per-stock explanations,
in three flavours: console/text, Markdown, and HTML (for email + web).
"""
from __future__ import annotations
from datetime import datetime


def _row(r: dict) -> str:
    return (
        f"{r['symbol']:<10} {r['signal']:<5} score={r['score']} "
        f"price={r['price']:<10} RS={r['rs']:+.1f}% "
        f"R:R={r['rr'] if r['rr'] else '-'}"
    )


def to_text(scan: dict) -> str:
    lines = [f"J Law Scanner — {datetime.now():%Y-%m-%d %H:%M} HKT", "=" * 60]
    for market, data in scan.items():
        lines.append(f"\n### {market.upper()}  (scanned {data['scanned']} symbols)")
        lines.append("-- BUY candidates --")
        for r in data["buys"]:
            lines.append(_row(r))
        lines.append("-- SELL candidates --")
        for r in data["sells"]:
            lines.append(_row(r))
        if data.get("watches"):
            lines.append("-- WATCH (confluence, but no tight entry) --")
            for r in data["watches"]:
                lines.append(_row(r))
    return "\n".join(lines)


def to_markdown(scan: dict) -> str:
    md = [f"# J Law Scanner Report — {datetime.now():%Y-%m-%d %H:%M} HKT\n"]
    for market, data in scan.items():
        md.append(f"## {market.upper()}  (scanned {data['scanned']} symbols)\n")
        md.append("### 🟢 BUY (M.E.T.A. confluence)\n")
        md.append("| Symbol | Score | Price | RS% | Entry | Stop | Target | R:R |")
        md.append("|---|---|---|---|---|---|---|---|")
        for r in data["buys"]:
            md.append(
                f"| {r['symbol']} | {r['score']} | {r['price']} | {r['rs']:+.1f} | "
                f"{r['entry']} | {r['stop']} | {r['target']} | {r['rr']} |"
            )
        md.append("\n### 🔴 SELL (weakness / distribution)\n")
        md.append("| Symbol | Bear edges | Price | RS% |")
        md.append("|---|---|---|---|")
        for r in data["sells"]:
            md.append(f"| {r['symbol']} | {len(r['bear_edges'])} | {r['price']} | {r['rs']:+.1f} |")
        if data.get("watches"):
            md.append("\n### 🟡 WATCH (confluence, but stop too wide)\n")
            md.append("| Symbol | Score | Price | Risk% | Stop source |")
            md.append("|---|---|---|---|---|")
            for r in data["watches"]:
                md.append(f"| {r['symbol']} | {r['score']} | {r['price']} | "
                          f"{r.get('risk_pct')} | {r.get('stop_source')} |")
        md.append("")
    return "\n".join(md)


def _explain_html(r: dict, img_cid: str | None = None) -> str:
    img = ""
    if img_cid:
        img = (
            f'<img src="{img_cid}" alt="{r["symbol"]} chart" '
            f'style="width:100%;max-width:560px;border:1px solid #2a2a2a;'
            f'border-radius:6px;margin:6px 0 2px;">'
        )
    items = "".join(f"<li>{e}</li>" for e in r["explanation"])
    return f"""
    <div style="border:1px solid #2a2a2a;border-radius:8px;padding:10px;margin:8px 0;">
      <b>{r['symbol']}</b> · {r['signal']} · score {r['score']} · RS {r['rs']:+.1f}%
      {img}
      <ul style="margin:6px 0 0;padding-left:18px;font-size:13px;">{items}</ul>
    </div>"""


def to_html(scan: dict, chart_cids: dict | None = None) -> str:
    chart_cids = chart_cids or {}
    parts = [f"<h2>J Law Scanner — {datetime.now():%Y-%m-%d %H:%M} HKT</h2>"]
    for market, data in scan.items():
        parts.append(f"<h3>{market.upper()} — scanned {data['scanned']} symbols</h3>")
        parts.append("<b>🟢 BUY</b>")
        for r in data["buys"]:
            parts.append(_explain_html(r, chart_cids.get(f"{market}:{r['symbol']}")))
        parts.append("<b>🔴 SELL</b>")
        for r in data["sells"]:
            parts.append(_explain_html(r, chart_cids.get(f"{market}:{r['symbol']}")))
    return (
        "<html><body style='font-family:system-ui,Arial,sans-serif;background:#111;"
        "color:#eee;padding:16px;'>"
        + "".join(parts)
        + "</body></html>"
    )
