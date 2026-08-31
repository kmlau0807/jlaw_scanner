"""
universe_builder.py — auto-refresh the HK / US large-cap watchlists.

HK
--
Pulls the live constituents of the Hang Seng Index (HSI) and the Hang Seng
China Enterprises Index (HSCEI) from Wikipedia, then builds `universe/hk.csv`.

US
--
Pulls the live constituents of the S&P 500 and the Nasdaq-100 from
Slickcharts (https://www.slickcharts.com), then builds `universe/us.csv`.

Both builders write the file as the UNION of:

    1. live index constituents           -> stays current automatically
    2. your existing <market>.csv        -> never loses a stock you curated
    3. MANUAL_<MARKET> overrides         -> names that are large-cap but not in
                                            those indices (e.g. HK 2268.HK ADRs)

This is non-destructive: a stock you already track is never dropped, and new
index constituents are added on every refresh. Stocks removed from an index
stay in the list (a watchlist, not a strict mirror) until you edit the file.

If the live fetch fails (network/site down), the existing file is kept
untouched and the run still succeeds. The hardcoded SEED_<MARKET> lists are
only used as a last-resort fallback when the CSV file does not exist at all.
"""
from __future__ import annotations

import logging
import os
import re
import shutil

import requests

log = logging.getLogger("jlaw.universe")

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; jlaw-universe-builder/1.0)"}
_TIMEOUT = 25

# --- HK sources / overrides -------------------------------------------------
WIKI_HSI = "https://en.wikipedia.org/wiki/Hang_Seng_Index"
WIKI_HSCEI = "https://en.wikipedia.org/wiki/Hang_Seng_China_Enterprises_Index"

# Names that are genuinely large-cap but are NOT in HSI/HSCEI.
# 2268.HK = WuXi XDC (药明合联), ADC biotech, mkt cap ~HK$82-91B (>= HK$50B threshold).
MANUAL_HK = [
    "0358.HK",   # Jiangxi Copper
    "1347.HK",   # Hua Hong Semiconductor
    "1772.HK",   # Ganfeng Lithium
    "1800.HK",   # China Communications Construction (CCCC)
    "1898.HK",   # China Coal Energy
    "1988.HK",   # Minsheng Bank
    "2268.HK",   # WuXi XDC
    "2333.HK",   # Great Wall Motor
    "2888.HK",   # Standard Chartered
    "3606.HK",   # Fuyao Glass
    "3908.HK",   # CICC
    "6030.HK",   # CITIC Securities
    "6160.HK",   # BeiGene (BeOne Medicines)
    "6881.HK",   # China Galaxy Securities (CGS)
    "6886.HK",   # HTSC
    "9698.HK",   # GDS-SW
]

SEED_HK = [
    "0005.HK", "0700.HK", "9988.HK", "3690.HK", "1810.HK", "0939.HK", "1398.HK",
    "3988.HK", "2318.HK", "2628.HK", "0941.HK", "0001.HK", "0002.HK", "0003.HK",
    "0006.HK", "0011.HK", "0012.HK", "0016.HK", "0017.HK", "0019.HK", "0023.HK",
    "0267.HK", "0386.HK", "0388.HK", "0390.HK", "0688.HK", "0762.HK", "0823.HK",
    "0857.HK", "0883.HK", "0960.HK", "0981.HK", "0992.HK", "1024.HK", "1088.HK",
    "1093.HK", "1109.HK", "1113.HK", "1177.HK", "1211.HK", "1299.HK", "1378.HK",
    "1801.HK", "1876.HK", "1928.HK", "2007.HK", "2018.HK", "2020.HK", "2313.HK",
    "2319.HK", "2328.HK", "2331.HK", "2359.HK", "2382.HK", "2388.HK", "2601.HK",
    "2688.HK", "2899.HK", "3328.HK", "3888.HK", "3993.HK", "6098.HK", "6618.HK",
    "6690.HK", "6862.HK", "9618.HK", "9626.HK", "9888.HK", "9901.HK", "9961.HK",
    "0004.HK", "0010.HK", "0027.HK", "0288.HK", "0669.HK", "0753.HK", "0853.HK",
    "0881.HK", "0909.HK", "0914.HK", "0933.HK", "1071.HK", "1108.HK", "1128.HK",
    "1157.HK", "1288.HK", "1929.HK", "2013.HK", "2600.HK", "3618.HK",
]

# --- US sources / overrides -------------------------------------------------
# Slickcharts publishes clean, identical tables for both indices.
SLICK_SP500 = "https://www.slickcharts.com/sp500"
SLICK_NDX100 = "https://www.slickcharts.com/nasdaq100"

# ADRs / foreign large-caps you track but which are NOT in the S&P 500 or
# Nasdaq-100 (they are already preserved via the existing us.csv union, so
# this list can stay empty — it is here only if you ever wipe us.csv).
MANUAL_US: list[str] = []

SEED_US = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "BRK-B",
    "JPM", "V", "MA", "UNH", "JNJ", "WMT", "PG", "XOM", "HD", "CVX", "BAC",
    "KO", "PEP", "COST", "ADBE", "CRM", "NFLX", "AMD", "INTC", "QCOM", "CSCO",
    "ORCL", "IBM", "TXN", "AVGO", "AMGN", "GILD", "ABBV", "LLY", "PFE", "MRK",
    "TMO", "ABT", "C", "WFC", "GS", "MS", "BLK", "AXP", "PYPL", "UBER", "MCD",
    "SBUX", "NKE", "BA", "GE", "CAT", "DE", "F", "GM", "VZ", "T", "DIS", "BABA",
]

_HK_CODE_RE = re.compile(r"^\d{4,5}\.HK$", re.I)
# US tickers: start with a letter, then letters/digits/./- (1-7 chars).
_US_CODE_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,6}$")


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------
def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s).strip()


def _read_existing(csv_path: str, code_re: re.Pattern) -> set[str]:
    out = set()
    if not os.path.exists(csv_path):
        return out
    with open(csv_path, encoding="utf-8-sig") as f:
        for line in f:
            s = line.strip()
            if s and code_re.match(s):
                out.add(s.upper())
    return out


def _union_and_write(csv_path, prev, live, manual, seed):
    """Shared non-destructive union+write. Returns a stats dict."""
    manual_set = {c.upper() for c in manual}
    combined = set(live) | prev | manual_set
    if not combined:  # absolute last resort
        combined = {c.upper() for c in seed}
        log.warning("empty universe; falling back to SEED (%d names)", len(combined))

    # Rolling single backup of the previous file before overwriting.
    if prev:
        bak = csv_path + ".bak"
        try:
            shutil.copy2(csv_path, bak)
        except OSError as e:
            log.warning("could not back up %s: %s", csv_path, e)

    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("symbol\n")
        for code in sorted(combined):
            f.write(code + "\n")

    added = sorted(combined - prev)
    return {
        "n_before": len(prev),
        "n_after": len(combined),
        "live_n": len(live),
        "added": added,
        "n_added": len(added),
        "manual_added": sorted(manual_set - prev),
        "source": "live" if live else "kept-existing",
    }


# ---------------------------------------------------------------------------
# HK: Wikipedia HSI + HSCEI
# ---------------------------------------------------------------------------
def _norm_hk_code(raw: str):
    m = re.search(r"(\d{1,5})", raw or "")
    if not m:
        return None
    n = m.group(1)
    return n.zfill(4) if len(n) <= 4 else n


def _parse_wiki_constituents(url: str) -> list[tuple[str, str]]:
    """Return [(code, name), ...] from a Wikipedia `id="Constituents"` table."""
    r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    html = r.text
    m = re.search(r'id="[Cc]onstituents"', html)
    if not m:
        raise RuntimeError(f"no constituents table on {url}")
    seg = html[m.start():]
    end = seg.find("</table>")
    if end != -1:
        seg = seg[:end]
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, flags=re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S)
        if len(cells) < 2:
            continue
        vals = [_strip_tags(c) for c in cells]
        code = _norm_hk_code(vals[0])
        if code and re.search(r"\d", vals[0]):
            out.append((code + ".HK", vals[1]))
    return out


def fetch_hk_constituents() -> dict[str, str]:
    """Best-effort union of HSI + HSCEI constituents. Missing source -> skipped."""
    found: dict[str, str] = {}
    for label, url in (("HSI", WIKI_HSI), ("HSCEI", WIKI_HSCEI)):
        try:
            rows = _parse_wiki_constituents(url)
            for code, name in rows:
                found.setdefault(code, name)
            log.info("fetched %s: %d constituents", label, len(rows))
        except Exception as e:  # noqa: BLE001
            log.warning("%s fetch failed: %s", label, e)
    return found


def build_hk_universe(csv_path: str, manual: list[str] | None = None) -> dict:
    prev = _read_existing(csv_path, _HK_CODE_RE)
    live = fetch_hk_constituents()
    return _union_and_write(
        csv_path, prev, set(live),
        manual if manual is not None else MANUAL_HK, SEED_HK,
    )


def refresh_hk_universe(project_root: str | None = None,
                        manual: list[str] | None = None) -> dict:
    if project_root is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(project_root, "universe", "hk.csv")
    return build_hk_universe(csv_path, manual=manual)


# ---------------------------------------------------------------------------
# US: Slickcharts S&P 500 + Nasdaq-100
# ---------------------------------------------------------------------------
def _norm_us_code(raw: str):
    s = (raw or "").strip().upper()
    # yfinance style: share class uses '-' (e.g. BRK.B -> BRK-B)
    s = s.replace(".", "-")
    if _US_CODE_RE.match(s):
        return s
    return None


def _parse_slickcharts(url: str) -> list[tuple[str, str]]:
    """Return [(symbol, company), ...] from a Slickcharts index table.

    The constituents table (TABLE 0) has a header row containing 'Symbol';
    the symbol is in that column for every data row."""
    r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    html = r.text
    for tbl in re.findall(r"<table.*?</table>", html, flags=re.S):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, flags=re.S)
        if len(rows) < 2:
            continue
        hdr = [_strip_tags(c) for c in
               re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", rows[0], flags=re.S)]
        # locate the 'Symbol' column (case-insensitive)
        sym_idx = next((i for i, h in enumerate(hdr) if h.lower() == "symbol"), None)
        if sym_idx is None:
            continue
        name_idx = next((i for i, h in enumerate(hdr) if h.lower() == "company"), None)
        out = []
        for row in rows[1:]:
            cells = [_strip_tags(c) for c in
                     re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S)]
            if len(cells) <= sym_idx:
                continue
            sym = _norm_us_code(cells[sym_idx])
            if not sym:
                continue
            name = cells[name_idx].strip() if (name_idx is not None and len(cells) > name_idx) else ""
            out.append((sym, name))
        if out:
            return out
    raise RuntimeError(f"no Symbol table found on {url}")


def fetch_us_constituents() -> dict[str, str]:
    """Best-effort union of S&P 500 + Nasdaq-100. Missing source -> skipped."""
    found: dict[str, str] = {}
    for label, url in (("S&P 500", SLICK_SP500), ("Nasdaq-100", SLICK_NDX100)):
        try:
            rows = _parse_slickcharts(url)
            for sym, name in rows:
                found.setdefault(sym, name)
            log.info("fetched %s: %d constituents", label, len(rows))
        except Exception as e:  # noqa: BLE001
            log.warning("%s fetch failed: %s", label, e)
    return found


def build_us_universe(csv_path: str, manual: list[str] | None = None) -> dict:
    prev = _read_existing(csv_path, _US_CODE_RE)
    live = fetch_us_constituents()
    return _union_and_write(
        csv_path, prev, set(live),
        manual if manual is not None else MANUAL_US, SEED_US,
    )


def refresh_us_universe(project_root: str | None = None,
                        manual: list[str] | None = None) -> dict:
    if project_root is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(project_root, "universe", "us.csv")
    return build_us_universe(csv_path, manual=manual)
