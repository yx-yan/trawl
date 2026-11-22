"""Command line: `trawl collect`, `trawl process`, `trawl tailor`, `trawl digest`, `trawl run` (all
four, for cron), and `trawl gaps`."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

from . import digest
from .config import HOME, Settings, Sources, load_dotenv
from .connectors import CONNECTORS
from .decide import Criteria
from .extract import new_client
from .match import load_resume, resume_path
from .models import Posting
from .process import process, redecide, tailor_new
from .store import Store
from .tailor import Compiler, Resume, load_bank, parse, source_hash, tailor, xelatex

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


def load_tailoring(settings: Settings) -> tuple[Resume, str, Compiler] | None:
    """The master résumé, bank and compiler for tailoring, or None (with the reason on stderr)."""
    try:
        tex = resume_path().read_text()
    except OSError:
        print(f"no résumé at {resume_path()} (TRAWL_RESUME): no tailored résumés", file=sys.stderr)
        return None
    if not (settings.resume_class / "resume-en.cls").is_file():
        print(f"no résumé class in {settings.resume_class} (TRAWL_RESUME_CLASS): no tailored résumés; "
              "see README", file=sys.stderr)
        return None
    bank = load_bank(settings.bank_path)
    return parse(tex, bank), source_hash(tex, bank), xelatex(settings.resume_class)


def tailor_ids(store: Store, settings: Settings, ids: list[int], out: Path, *, again: bool) -> None:
    """Tailor a résumé for each posting id (whatever its score) and write it to out/<id>-<company>/."""
    loaded = load_tailoring(settings)
    if loaded is None:
        sys.exit(1)
    resume, rhash, compile_tex = loaded
    client = None
    for pid in ids:
        p = store.posting(pid)
        if p is None:
            print(f"#{pid}: no such posting", file=sys.stderr)
            continue
        app = None if again else store.application(pid, rhash)
        if app is None:
            client = client or new_client()
            r = tailor(client, resume, compile_tex, company=p.company, title=p.title,
                       description=p.description, fit_hint=p.match)
            store.save_application(pid, rhash, r.record(), r.tex, r.pdf)
            app = store.application(pid, rhash)
        folder = out / f"{pid}-{re.sub(r'[^a-z0-9]+', '-', p.company.lower()).strip('-')}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "resume.pdf").write_bytes(app.resume_pdf)
        (folder / "resume.tex").write_text(app.resume_tex)
        (folder / "tailoring.json").write_text(json.dumps(app.tailoring, indent=2, ensure_ascii=False))
        print(digest.caption(app, limit=10_000))
        print(f"  -> {folder / 'resume.pdf'}\n")


def send_digest(store: Store, settings: Settings, *, dry_run: bool) -> None:
    batch = store.undigested()
    apps = store.unsent_applications([r.id for r in batch.rows + batch.resurfaced])
    text = digest.render(batch)
    if dry_run or not (settings.telegram_token and settings.telegram_chat_id):
        print(text)
        for app in apps:
            print(f"\n[tailored résumé] {digest.caption(app)}")
        if not dry_run:
            # Only what was really sent is marked, so these stay for the first real digest
            print("(TRAWL_TELEGRAM_TOKEN / TRAWL_TELEGRAM_CHAT_ID not set: printed, nothing marked as sent)",
                  file=sys.stderr)
        return
    digest.send_telegram(text, settings.telegram_token, settings.telegram_chat_id)
    store.mark_digested(batch)
    for app in apps:  # a résumé that fails to send goes out with the next digest
        name = f"resume-{re.sub(r'[^a-z0-9]+', '-', app.company.lower()).strip('-')}-{app.posting_id}.pdf"
        try:
            digest.send_document(name, app.resume_pdf, digest.caption(app), settings.telegram_token,
                                 settings.telegram_chat_id)
        except httpx.HTTPError as e:
            print(f"  sending the résumé for #{app.posting_id} failed: {e}", file=sys.stderr)
            continue
        store.mark_application_sent(app.posting_id)


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
    t = sub.add_parser("tailor", help="tailor the résumé to jobs: the best new fits, or the ids given")
    t.add_argument("ids", nargs="*", type=int, help="posting ids (shown as #id in the digest)")
    t.add_argument("--out", type=Path, default=HOME / "applications",
                   help="where ids given write their résumés (default: ~/.trawl/applications)")
    t.add_argument("--again", action="store_true", help="tailor again even if this résumé was already done")
    g = sub.add_parser("gaps", help="skills eligible jobs ask for that the résumé doesn't show")
    g.add_argument("--send", action="store_true", help="send to Telegram instead of printing")
    sub.add_parser("run", help="collect, process, tailor, then digest (what cron runs)")
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
    if args.command == "tailor" and args.ids:
        tailor_ids(store, settings, args.ids, args.out, again=args.again)
    elif args.command in ("tailor", "run") and (loaded := load_tailoring(settings)):
        done, _ = tailor_new(store, new_client(), *loaded, min_score=settings.tailor_min_score,
                             limit=settings.tailor_limit)
        print(f"tailored {done} résumé{'s' if done != 1 else ''}")
    if args.command in ("digest", "run"):
        send_digest(store, settings, dry_run=getattr(args, "dry_run", False))
    if args.command == "gaps":
        text = digest.render_gaps(store.gap_counts())
        if args.send and settings.telegram_token and settings.telegram_chat_id:
            digest.send_telegram(text, settings.telegram_token, settings.telegram_chat_id)
        else:
            print(text)
