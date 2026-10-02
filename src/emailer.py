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
import time
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


def _smtp_send(cfg: dict, msg, retries: int = 3, delay: int = 15) -> bool:
    """Open a fresh Gmail SMTP connection and send `msg`.

    Retries on transient failures (e.g. 'Server not connected', timeouts)
    so a single dropped connection does not sink the whole daily report.
    """
    recipients = [r.strip() for r in cfg["recipients"].split(",") if r.strip()]
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=30) as s:
                s.ehlo()
                s.starttls()
                s.ehlo()
                s.login(cfg["sender"], cfg["password"])
                s.sendmail(cfg["sender"], recipients, msg.as_string())
            return True
        except Exception as e:  # noqa: BLE001
            last_exc = e
            logger.warning("email attempt %d/%d failed: %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(delay)
    logger.error("email failed after %d attempts: %s", retries, last_exc)
    return False


def send(scan: dict, cfg: dict, alerts: dict | None = None) -> bool:
    chart_top_n = int(cfg.get("chart_top_n", 5))
    images, cids = _build_images(scan, chart_top_n)
    html = rep.to_html(scan, chart_cids=cids)
    text = rep.to_text(scan)
    # Phase 3: append the user's holdings price-alerts block (if any).
    if alerts:
        html += alerts.get("html", "")
        text += alerts.get("text", "")
    cfg = dict(cfg)
    cfg["date"] = ""
    msg = build_message(cfg, html, text, images)
    ok = _smtp_send(cfg, msg)
    if ok:
        n_alerts = len(alerts.get("html", "")) if alerts else 0
        logger.info("email sent to %s (%d charts, alerts=%s)",
                    cfg["recipients"], len(images), bool(alerts))
    return ok


def send_test(cfg: dict) -> bool:
    html = "<p>This is a test email from the J Law Scanner.</p>"
    text = "J Law Scanner test email."
    cfg = dict(cfg)
    cfg["date"] = "(test)"
    msg = build_message(cfg, html, text)
    ok = _smtp_send(cfg, msg)
    if not ok:
        logger.error("test email failed")
    return ok
