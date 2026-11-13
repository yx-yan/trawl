"""Command line: `trawl collect`, `trawl digest`, and `trawl run` (both, for cron)."""

from __future__ import annotations

import argparse
import sys

import httpx

from . import digest
from .config import Settings, Sources, load_dotenv
from .connectors import CONNECTORS
from .models import Posting
from .store import Store
from .titles import in_locations

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
            wanted = [p for p in postings if in_locations(p.location, sources.locations)]
            new = store.upsert(wanted)
            total_new += new
            print(f"  {name}/{key}: {len(postings)} postings, {len(wanted)} in location, {new} new")
    return total_new


def send_digest(store: Store, settings: Settings, *, dry_run: bool) -> None:
    rows = store.undigested()
    text = digest.render(rows)
    if dry_run or not (settings.telegram_token and settings.telegram_chat_id):
        print(text)
        if not dry_run:
            # Only what was really sent is marked, so these stay for the first real digest
            print("(TRAWL_TELEGRAM_TOKEN / TRAWL_TELEGRAM_CHAT_ID not set: printed, nothing marked as sent)",
                  file=sys.stderr)
        return
    digest.send_telegram(text, settings.telegram_token, settings.telegram_chat_id)
    store.mark_digested(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="trawl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect", help="fetch all boards in sources.toml into the database")
    d = sub.add_parser("digest", help="send new title-matched jobs to Telegram")
    d.add_argument("--dry-run", action="store_true", help="print, and don't mark anything as sent")
    sub.add_parser("run", help="collect, then digest (what cron runs)")
    args = parser.parse_args(argv)

    load_dotenv()
    settings = Settings.from_env()
    store = Store.connect(settings.database_url)
    if args.command in ("collect", "run"):
        sources = Sources.load(settings.sources_path)
        with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}) as http:
            new = collect(store, sources, http)
        print(f"{new} new postings")
    if args.command in ("digest", "run"):
        send_digest(store, settings, dry_run=getattr(args, "dry_run", False))
