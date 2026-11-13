"""Command line: `trawl collect`, `trawl process`, `trawl digest`, `trawl run` (all three, for cron),
and `trawl gaps`."""

from __future__ import annotations

import argparse
import sys

import httpx

from . import digest
from .config import Settings, Sources, load_dotenv
from .connectors import CONNECTORS
from .decide import Criteria
from .extract import new_client
from .match import load_resume, resume_path
from .models import Posting
from .process import process, redecide
from .store import Store

USER_AGENT = "trawl/0.1 (+https://github.com/yx-yan/trawl)"


def collect(store: Store, sources: Sources, http: httpx.Client) -> int:
    """Fetch every configured board; one failing board is reported and skipped."""
    total_new = 0
    for name, keys in sources.boards.items():
        fetch = CONNECTORS[name]
        for key in keys:
            try:
                postings: list[Posting] = fetch(http, key)
            except (httpx.HTTPError, KeyError, ValueError) as e:
                print(f"  {name}/{key}: failed: {e}", file=sys.stderr)
                continue
            wanted = [p for p in postings if sources.wants(p.location)]
            new = store.upsert(wanted)
            total_new += new
            print(f"  {name}/{key}: {len(postings)} postings, {len(wanted)} in location, {new} new")
    return total_new


def send_digest(store: Store, settings: Settings, *, dry_run: bool) -> None:
    batch = store.undigested()
    text = digest.render(batch)
    if dry_run or not (settings.telegram_token and settings.telegram_chat_id):
        print(text)
        if not dry_run:
            # Only what was really sent is marked, so these stay for the first real digest
            print("(TRAWL_TELEGRAM_TOKEN / TRAWL_TELEGRAM_CHAT_ID not set: printed, nothing marked as sent)",
                  file=sys.stderr)
        return
    digest.send_telegram(text, settings.telegram_token, settings.telegram_chat_id)
    store.mark_digested(batch)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="trawl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect", help="fetch all boards in sources.toml into the database")
    p = sub.add_parser("process", help="give new postings a verdict (rules, then the model)")
    p.add_argument("--redecide", action="store_true",
                   help="re-apply criteria.toml to every posting first (no model calls for those)")
    p.add_argument("--reread", action="store_true",
                   help="have the model read every posting again (after changing extract.py)")
    d = sub.add_parser("digest", help="send new eligible jobs to Telegram")
    d.add_argument("--dry-run", action="store_true", help="print, and don't mark anything as sent")
    g = sub.add_parser("gaps", help="skills eligible jobs ask for that the résumé doesn't show")
    g.add_argument("--send", action="store_true", help="send to Telegram instead of printing")
    sub.add_parser("run", help="collect, process, then digest (what cron runs)")
    args = parser.parse_args(argv)

    load_dotenv()
    settings = Settings.from_env()
    store = Store.connect(settings.database_url)
    criteria = Criteria.load(settings.criteria_path)
    if not settings.criteria_path.is_file():
        print(f"no criteria at {settings.criteria_path} (TRAWL_CRITERIA): using neutral defaults; "
              "see criteria.example.toml", file=sys.stderr)
    with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}) as http:
        if args.command in ("collect", "run"):
            new = collect(store, Sources.load(settings.sources_path), http)
            print(f"{new} new postings")
        if args.command in ("process", "run"):
            if getattr(args, "reread", False):
                store.forget_job_facts()
            elif getattr(args, "redecide", False):
                print(f"re-decided {redecide(store, criteria)} postings")
            resume = None
            try:
                resume = load_resume(resume_path())
            except OSError:
                print(f"no résumé at {resume_path()} (TRAWL_RESUME): jobs judged but not scored",
                      file=sys.stderr)
            print(process(store, criteria, http, new_client(), resume))
    if args.command in ("digest", "run"):
        send_digest(store, settings, dry_run=getattr(args, "dry_run", False))
    if args.command == "gaps":
        text = digest.render_gaps(store.gap_counts())
        if args.send and settings.telegram_token and settings.telegram_chat_id:
            digest.send_telegram(text, settings.telegram_token, settings.telegram_chat_id)
        else:
            print(text)
