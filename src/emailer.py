"""
emailer.py
----------
Sends the HTML scan report via Gmail SMTP (TLS, app password).
All settings come from config.ini — no secrets in code.

The report now embeds a TA chart per top pick (inline CID image), generated
from the same cached data the web UI uses, so Phase 1 email is chart-supported
without needing the web app.
"""
from __future__ import annotations
import base64
import logging
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

from . import reporter as rep
from . import chart as chartmod

logger = logging.getLogger("jlaw.email")

_CID_RE = re.compile(r"[^A-Za-z0-9]")


def _cid_for(market: str, symbol: str) -> str:
    return "chart_" + _CID_RE.sub("_", f"{market}_{symbol}")


def _build_images(scan: dict, top_n: int):
    """Generate charts for the top `top_n` picks per side per market.

    Returns (images, cids) where images = [(cid, png_bytes), ...] and
    cids maps 'market:symbol' -> 'cid:<cid>' for the HTML.
    """
    images = []
    cids = {}
    for market, data in scan.items():
        for side in ("buys", "sells"):
            for r in data.get(side, [])[:top_n]:
                b64 = chartmod.build_chart(r["symbol"], market, r)
                if not b64:
                    continue
                raw = base64.b64decode(b64.split(",", 1)[1])
                cid = _cid_for(market, r["symbol"])
                images.append((cid, raw))
                cids[f"{market}:{r['symbol']}"] = f"cid:{cid}"
    return images, cids


def build_message(cfg: dict, html: str, text: str, images=None) -> MIMEMultipart:
    msg = MIMEMultipart("related")
    msg["Subject"] = f"{cfg['subject_prefix']} {cfg.get('date', '')}".strip()
    msg["From"] = cfg["sender"]
    msg["To"] = cfg["recipients"]
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)
    for cid, data in (images or []):
        img = MIMEImage(data)
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
        msg.attach(img)
    return msg


def send(scan: dict, cfg: dict) -> bool:
    chart_top_n = int(cfg.get("chart_top_n", 5))
    images, cids = _build_images(scan, chart_top_n)
    html = rep.to_html(scan, chart_cids=cids)
    text = rep.to_text(scan)
    cfg = dict(cfg)
    cfg["date"] = ""
    msg = build_message(cfg, html, text, images)
    try:
        with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(cfg["sender"], cfg["password"])
            s.sendmail(cfg["sender"], cfg["recipients"].split(","), msg.as_string())
        logger.info("email sent to %s (%d charts)", cfg["recipients"], len(images))
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("email failed: %s", e)
        return False


def send_test(cfg: dict) -> bool:
    html = "<p>This is a test email from the J Law Scanner.</p>"
    text = "J Law Scanner test email."
    cfg = dict(cfg)
    cfg["date"] = "(test)"
    msg = build_message(cfg, html, text)
    try:
        with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=30) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(cfg["sender"], cfg["password"])
            s.sendmail(cfg["sender"], cfg["recipients"].split(","), msg.as_string())
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("test email failed: %s", e)
        return False
