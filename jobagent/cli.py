"""Command line entry point: python -m jobagent <command>"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import time

from . import db
from .config import DATA_DIR, DEFAULT_CONFIG, EXAMPLE_CONFIG, ROOT, ConfigError, load_settings


def _setup_logging(verbose: bool) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler(DATA_DIR / "agent.log", encoding="utf-8")],
    )


def cmd_init(args: argparse.Namespace) -> None:
    if DEFAULT_CONFIG.exists() and not args.force:
        print(f"{DEFAULT_CONFIG} already exists (use --force to overwrite).")
        return
    shutil.copy(EXAMPLE_CONFIG, DEFAULT_CONFIG)
    db.connect().close()
    print(f"Created {DEFAULT_CONFIG}. Edit it, put your resume next to it, then log in to each site:")
    print("  python -m jobagent login linkedin")


def cmd_login(args: argparse.Namespace) -> None:
    from .browser import BrowserSession
    from .platforms import PLATFORMS

    settings = load_settings(args.config)
    cls = PLATFORMS[args.platform]
    adapter = cls(settings)
    urls = adapter.login_urls()
    if not urls:
        print(f"{args.platform} doesn't need a login.")
        return
    with BrowserSession(adapter.profile, headless=False) as session:
        page = session.new_page()
        for url in urls:
            page.goto(url)
            input(f"Log in (or create an account) at {url} in the browser window, then press Enter here... ")
        ok = adapter.is_logged_in(page)
    print("Session saved." if ok else "Still not logged in. Try again.")


def cmd_discover(args: argparse.Namespace) -> None:
    from .agents import run_discovery

    print(run_discovery(load_settings(args.config), args.platform, headless=args.headless))


def cmd_match(args: argparse.Namespace) -> None:
    from .agents import match_pending, rescore_all

    settings = load_settings(args.config)
    conn = db.connect()
    print(rescore_all(conn, settings) if args.rescore else match_pending(conn, settings))


def cmd_apply(args: argparse.Namespace) -> None:
    from .agents import run_applications

    settings = load_settings(args.config)
    print(run_applications(settings, args.platform, headless=args.headless, dry_run=args.dry_run, limit=args.limit))


def cmd_track(args: argparse.Namespace) -> None:
    from .agents import run_tracker

    print(run_tracker(load_settings(args.config)))


def cmd_run(args: argparse.Namespace) -> None:
    from .agents import run_applications, run_discovery, run_tracker

    log = logging.getLogger("jobagent")
    while True:
        settings = load_settings(args.config)  # re-read so config edits apply without restarting
        if args.auto_apply:
            # Same as review_mode = false: matched jobs are applied to without waiting for approval.
            settings.raw.setdefault("limits", {})["review_mode"] = False
            # Also release jobs already waiting in the review queue.
            conn = db.connect()
            for row in db.jobs_with_status(conn, "matched"):
                db.set_status(conn, row["id"], "approved", "Auto-approved (--auto-apply)")
            conn.close()
        apply_step = ("apply", run_applications, dict(platforms=args.platform, headless=args.headless, dry_run=args.dry_run))
        steps = [
            # Apply to jobs already approved first, so a slow discovery round doesn't hold them back.
            apply_step,
            ("discover", run_discovery, dict(platforms=args.platform, headless=args.headless)),
            apply_step,
            ("track", run_tracker, {}),
        ]
        for step, fn, kwargs in steps:
            try:
                log.info("== %s: %s", step, fn(settings, **kwargs))
            except Exception as exc:
                log.error("== %s failed: %s", step, exc)
        if not args.loop_minutes:
            break
        log.info("Sleeping %d minutes...", args.loop_minutes)
        time.sleep(args.loop_minutes * 60)


def cmd_status(args: argparse.Namespace) -> None:
    conn = db.connect()
    for status, n in db.status_counts(conn).items():
        print(f"{status:16} {n}")


def _streamlit(env: dict[str, str] | None = None) -> None:
    # Run from the project folder so .streamlit/config.toml (the theme) is picked up.
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "dashboard" / "app.py")],
        cwd=ROOT, env=env, check=False,
    )


def cmd_dashboard(args: argparse.Namespace) -> None:
    _streamlit()


def cmd_demo(args: argparse.Namespace) -> None:
    import os

    from .demo import seed

    demo_dir = ROOT / "data-demo"
    seed(demo_dir / "jobagent.db")
    print(f"Sample data written to {demo_dir}. Your real data in {DATA_DIR} is untouched.")
    env = dict(os.environ, JOBAGENT_DATA_DIR=str(demo_dir), JOBAGENT_DEMO="1", JOBAGENT_CONFIG=str(EXAMPLE_CONFIG))
    _streamlit(env)


def build_parser() -> argparse.ArgumentParser:
    from .platforms import PLATFORMS

    p = argparse.ArgumentParser(prog="jobagent", description="Rule-based job application agent")
    p.add_argument("--config", help=f"Config file (default: {DEFAULT_CONFIG})")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="Create config/profile.toml from the example")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("login", help="Open a browser to log in to a site once")
    s.add_argument("platform", choices=sorted(PLATFORMS))
    s.set_defaults(func=cmd_login)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--platform", action="append", choices=sorted(PLATFORMS), help="Limit to this platform (repeatable)")
        sp.add_argument("--headless", action="store_true", help="Hide the browser (CAPTCHAs can't be handled)")

    s = sub.add_parser("discover", help="Find new jobs and score them")
    add_common(s)
    s.set_defaults(func=cmd_discover)

    s = sub.add_parser("match", help="Score discovered jobs")
    s.add_argument("--rescore", action="store_true", help="Re-score matched and skipped jobs after editing preferences")
    s.set_defaults(func=cmd_match)

    s = sub.add_parser("apply", help="Apply to approved jobs")
    add_common(s)
    s.add_argument("--dry-run", action="store_true", help="Fill forms but don't submit")
    s.add_argument("--limit", type=int, help="Max applications this run")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("track", help="Read your inbox and update statuses")
    s.set_defaults(func=cmd_track)

    s = sub.add_parser("run", help="Discover, apply and track in one go")
    add_common(s)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--loop-minutes", type=int, default=0, help="Repeat every N minutes")
    s.add_argument("--auto-apply", action="store_true", help="Apply to matched jobs without review (overrides review_mode)")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("status", help="Show counts per status")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("dashboard", help="Open the web dashboard")
    s.set_defaults(func=cmd_dashboard)

    s = sub.add_parser("demo", help="Open the dashboard with sample data (doesn't touch your real data)")
    s.set_defaults(func=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        args.func(args)
    except ConfigError as exc:
        sys.exit(f"Config error: {exc}")
    except KeyboardInterrupt:
        sys.exit("Stopped.")
