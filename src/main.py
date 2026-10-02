"""
main.py — command line entry for Phase 1 (headless scanner).

Usage
-----
  python run.py scan                 # print the top-10 buy/sell table
  python run.py scan --email        # scan + send the report by email
  python run.py email-test          # send a test email (checks SMTP creds)
  python run.py schedule            # run the daily scheduler (APScheduler)
  python run.py refresh-hk          # refresh universe/hk.csv from live HSI+HSCEI
  python run.py refresh-us          # refresh universe/us.csv from live S&P 500 + Nasdaq-100

Config is read from config.ini in the project root.
"""
from __future__ import annotations
import argparse
import configparser
import logging
import os
import sys

# allow running as `python run.py` from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import scanner as scn
from src import reporter as rep
from src import emailer
from src import universe_builder as ub
from src import data_provider as dp

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("jlaw.main")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config() -> dict:
    cp = configparser.ConfigParser()
    cp.read(os.path.join(ROOT, "config.ini"))
    cfg = {}
    cfg.update(dict(cp["email"]))
    cfg.update(dict(cp["scan"]))
    cfg.update(dict(cp["schedule"]))
    cfg.update(dict(cp["paths"]))
    if cp.has_section("macd"):
        cfg["macd"] = dict(cp["macd"])
    if cp.has_section("strong_trend"):
        cfg["strong_trend"] = dict(cp["strong_trend"])
    if cp.has_section("data"):
        cfg["data"] = dict(cp["data"])
        dp.configure(cfg["data"])
    return cfg


def parse_markets(cfg: dict) -> list[str]:
    return [m.strip() for m in cfg.get("markets", "hk,us").split(",") if m.strip()]


def rs_map(cfg: dict) -> dict:
    return {"us": cfg.get("rs_index_us"), "hk": cfg.get("rs_index_hk")}


def do_scan(cfg: dict, send_email: bool = False):
    markets = parse_markets(cfg)
    scan = scn.scan_all(
        markets,
        top_n=int(cfg.get("top_n", 10)),
        min_edges=int(cfg.get("min_metascore", 3)),
        lookback_days=int(cfg.get("lookback_days", 180)),
        min_avg_dollar_volume=float(cfg.get("min_avg_dollar_volume", 5_000_000)),
        rs_map=rs_map(cfg),
        macd_cfg=cfg.get("macd"),
        st_cfg=cfg.get("strong_trend"),
    )
    print(rep.to_text(scan))
    # persist for the web UI (Phase 2) to read without re-scanning.
    # Retry a few times: on Windows the file can be momentarily locked by an
    # editor / AV scan the user has open, which must not silently drop the data.
    try:
        import json as _json
        import time as _time

        path = os.path.join(ROOT, "scan_result.json")
        data = _json.dumps(scan, ensure_ascii=False, indent=2, default=str)
        last_err = None
        for _ in range(5):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(data)
                break
            except (PermissionError, OSError) as e:  # noqa: BLE001
                last_err = e
                _time.sleep(0.5)
        else:
            log.warning("could not persist scan_result.json: %s", last_err)
    except Exception as e:  # noqa: BLE001
        log.warning("could not persist scan_result.json: %s", e)
    if send_email:
        # Phase 3: surface any holdings that hit / are near their target.
        alerts = None
        try:
            from src import bookkeeping as bk

            al = bk.compute_alerts()
            if al:
                alerts = {
                    "html": bk.build_alerts_html(al),
                    "text": bk.build_alerts_text(al),
                }
        except Exception:  # noqa: BLE001
            log.warning("could not build holdings alerts", exc_info=True)
        ok = emailer.send(scan, cfg, alerts=alerts)
        print("EMAIL:", "sent" if ok else "FAILED (check config.ini credentials)")
    return scan


def main(argv=None):
    p = argparse.ArgumentParser(description="J Law M.E.T.S./M.E.T.A. stock scanner")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("scan").add_argument("--email", action="store_true")
    sub.add_parser("email-test")
    sub.add_parser("schedule")
    sub.add_parser("refresh-hk")
    sub.add_parser("refresh-us")
    args = p.parse_args(argv)

    cfg = load_config()

    if args.cmd in (None, "scan"):
        do_scan(cfg, send_email=getattr(args, "email", False))
    elif args.cmd == "email-test":
        ok = emailer.send_test(cfg)
        print("TEST EMAIL:", "sent" if ok else "FAILED (check config.ini credentials)")
    elif args.cmd == "schedule":
        from src import scheduler as sch

        def job():
            do_scan(cfg, send_email=True)

        sch.run_loop(
            job,
            int(cfg.get("cron_hour", 8)),
            int(cfg.get("cron_minute", 30)),
            cfg.get("timezone", "Asia/Hong_Kong"),
        )
    elif args.cmd == "refresh-hk":
        stats = ub.refresh_hk_universe()
        print(f"HK universe refreshed: {stats['n_before']} -> {stats['n_after']} "
              f"(live sources={stats['live_n']}, added={stats['n_added']})")
        if stats["manual_added"]:
            print("manual overrides added:", ", ".join(stats["manual_added"]))
        if stats["added"]:
            print("newly added:", ", ".join(stats["added"]))
    elif args.cmd == "refresh-us":
        stats = ub.refresh_us_universe()
        print(f"US universe refreshed: {stats['n_before']} -> {stats['n_after']} "
              f"(live sources={stats['live_n']}, added={stats['n_added']})")
        if stats["manual_added"]:
            print("manual overrides added:", ", ".join(stats["manual_added"]))
        if stats["added"]:
            print("newly added:", ", ".join(stats["added"]))


if __name__ == "__main__":
    main()
