"""Dump the HK universe with human-readable names into an HTML page.

Reads universe/hk.csv, resolves each code to a company name using the same
Wikipedia HSI / HSCEI constituent tables the universe builder scrapes, plus
the curated MANUAL_HK labels. Emits hk_universe.html.
"""
from __future__ import annotations

import csv
import html
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from src import universe_builder as ub  # noqa: E402

CSV = os.path.join(HERE, "universe", "hk.csv")
OUT = os.path.join(HERE, "hk_universe.html")

MANUAL_LABELS = {
    "0358.HK": "Jiangxi Copper 江西銅業",
    "1347.HK": "Hua Hong Semiconductor 華虹半導體",
    "1772.HK": "Ganfeng Lithium 贛鋒鋰業",
    "1800.HK": "China Communications Construction 中國交通建設",
    "1898.HK": "China Coal Energy 中國中煤能源",
    "1988.HK": "Minsheng Bank 民生銀行",
    "2268.HK": "WuXi XDC 藥明合聯",
    "2333.HK": "Great Wall Motor 長城汽車",
    "2888.HK": "Standard Chartered 渣打集團",
    "3606.HK": "Fuyao Glass 福耀玻璃",
    "3908.HK": "CICC 中金公司",
    "6030.HK": "CITIC Securities 中信証券",
    "6160.HK": "BeiGene / BeOne Medicines 百濟神州",
    "6881.HK": "China Galaxy Securities 中國銀河證券",
    "6886.HK": "HTSC 華泰證券",
    "9698.HK": "GDS-SW 萬國數據",
}


def load_codes() -> list[str]:
    with open(CSV, encoding="utf-8-sig") as f:
        return [r["symbol"].strip() for r in csv.DictReader(f) if r.get("symbol")]


def main() -> None:
    codes = load_codes()

    hsi: dict[str, str] = {}
    hscei: dict[str, str] = {}
    for label, url, sink in (
        ("HSI", ub.WIKI_HSI, hsi),
        ("HSCEI", ub.WIKI_HSCEI, hscei),
    ):
        try:
            for code, name in ub._parse_wiki_constituents(url):
                sink.setdefault(code, name)
            print(f"{label}: {len(sink)} constituents")
        except Exception as e:  # noqa: BLE001
            print(f"{label} fetch failed: {e}", file=sys.stderr)

    manual = {c.upper() for c in ub.MANUAL_HK}

    rows = []
    for c in codes:
        name = hsi.get(c) or hscei.get(c) or MANUAL_LABELS.get(c) or "—"
        tags = []
        if c in hsi:
            tags.append("HSI")
        if c in hscei:
            tags.append("HSCEI")
        if c in manual:
            tags.append("manual")
        if not tags:
            tags.append("carried-over")
        rows.append((c, name, tags))

    n_hsi = sum(1 for _, _, t in rows if "HSI" in t)
    n_hscei = sum(1 for _, _, t in rows if "HSCEI" in t)
    n_manual = sum(1 for _, _, t in rows if "manual" in t)
    n_other = sum(1 for _, _, t in rows if "carried-over" in t)

    trs = "\n".join(
        f'<tr><td class="code">{html.escape(c)}</td>'
        f'<td>{html.escape(n)}</td>'
        f'<td>{"".join(f"<span class=\'tag t-{x.lower()}\'>{x}</span>" for x in t)}</td></tr>'
        for c, n, t in rows
    )

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>jlaw_scanner — HK universe</title>
<style>
:root{{--bg:#161616;--panel:#1e1e1e;--line:#2e2e2e;--fg:#e8e8e6;--muted:#9a9a95;--accent:#5DCAA5;}}
*{{box-sizing:border-box}}
body{{margin:0;padding:32px;background:var(--bg);color:var(--fg);
font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}}
.wrap{{max-width:900px;margin:0 auto}}
h1{{font-size:20px;font-weight:500;margin:0 0 4px}}
.sub{{color:var(--muted);font-size:13px;margin:0 0 24px}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin:0 0 24px}}
.stat{{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:12px 16px;min-width:120px}}
.stat b{{display:block;font-size:22px;font-weight:500}}
.stat span{{color:var(--muted);font-size:12px}}
.note{{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:8px;padding:14px 16px;margin:0 0 24px;color:var(--muted);font-size:13px}}
.note b{{color:var(--fg);font-weight:500}}
table{{width:100%;border-collapse:collapse;background:var(--panel);
border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th{{text-align:left;font-weight:500;font-size:12px;color:var(--muted);
padding:10px 14px;border-bottom:1px solid var(--line);text-transform:uppercase;letter-spacing:.04em}}
td{{padding:8px 14px;border-bottom:1px solid var(--line)}}
tr:last-child td{{border-bottom:none}}
.code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--accent)}}
.tag{{display:inline-block;font-size:11px;padding:1px 7px;border-radius:20px;margin-right:4px;
border:1px solid var(--line);color:var(--muted)}}
.t-hsi{{border-color:#378ADD;color:#85B7EB}}
.t-hscei{{border-color:#BA7517;color:#EF9F27}}
.t-manual{{border-color:#5DCAA5;color:#5DCAA5}}
</style></head><body><div class="wrap">
<h1>HK market stock pool</h1>
<p class="sub">jlaw_scanner · universe/hk.csv · {len(rows)} symbols</p>
<div class="stats">
<div class="stat"><b>{len(rows)}</b><span>total symbols</span></div>
<div class="stat"><b>{n_hsi}</b><span>in Hang Seng Index</span></div>
<div class="stat"><b>{n_hscei}</b><span>in HSCEI</span></div>
<div class="stat"><b>{n_manual}</b><span>manual additions</span></div>
<div class="stat"><b>{n_other}</b><span>carried over only</span></div>
</div>
<div class="note">
<b>How the pool is built.</b> <code>src/universe_builder.py</code> scrapes the constituent
tables from Wikipedia's Hang Seng Index and Hang Seng China Enterprises Index pages,
forces in a curated <b>MANUAL_HK</b> list of large caps that are in neither index, and then
<b>unions</b> that with whatever is already in <code>universe/hk.csv</code>. The union is
non-destructive — names are never removed, so the list only ever grows. A single rolling
backup is kept at <code>hk.csv.bak</code>. At scan time each name still has to clear the
liquidity gate: 20-day average dollar volume ≥ <b>HK$5M</b> (config.ini <code>min_avg_dollar_volume</code>).
</div>
<table><thead><tr><th>Code</th><th>Company</th><th>Source</th></tr></thead>
<tbody>
{trs}
</tbody></table>
</div></body></html>"""

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"wrote {OUT} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
