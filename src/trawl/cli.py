"""Command line: `trawl collect`, `trawl process`, `trawl digest`, `trawl run` (collect, process
and digest, for cron), `trawl gaps`, and on the Mac: `trawl tailor` (on demand), `trawl profile`
(the encrypted answers that fill forms), `trawl apply` (fill a form in Chrome; submit once you
approve it) and `trawl app` (the desktop app)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx

from . import answer, digest
from . import profile as profiles
from .config import HOME, Settings, Sources, load_dotenv
from .connectors import CONNECTORS
from .decide import Criteria
from .extract import new_client
from .match import load_resume, resume_path
from .models import Posting
from .process import prepare_one, process, redecide, tailor_new
from .store import Store
from .tailor import Compiler, Resume, load_bank, parse, source_hash, xelatex

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


def load_profile(settings: Settings) -> profiles.Profile | None:
    """The decrypted profile, or None (with the reason on stderr): forms are still drafted, and
    every profile question then waits for the person."""
    try:
        profile = profiles.load(settings.profile_path)
    except RuntimeError as e:
        print(f"profile: {e}", file=sys.stderr)
        return None
    if profile is None:
        print(f"no profile at {settings.profile_path}: run `trawl profile edit`", file=sys.stderr)
    return profile


def tailor_ids(store: Store, settings: Settings, http: httpx.Client, ids: list[int], out: Path, *,
               again: bool) -> None:
    """Prepare each posting id (whatever its score) and write it to out/<id>-<company>/."""
    loaded = load_tailoring(settings)
    if loaded is None:
        sys.exit(1)
    profile = load_profile(settings)
    clients: list = []

    def client():  # noqa: ANN202 - one model client, made only if something needs tailoring
        clients[:] = clients or [new_client()]
        return clients[0]

    for pid in ids:
        p = store.posting(pid)
        if p is None:
            print(f"#{pid}: no such posting", file=sys.stderr)
            continue
        app, note = prepare_one(store, client, loaded, profile, http, pid, again=again)
        if note:
            print(f"#{pid}: form: {note}", file=sys.stderr)
        folder = out / f"{pid}-{_slug(p.company)}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "resume.pdf").write_bytes(app.resume_pdf)
        (folder / "resume.tex").write_text(app.resume_tex)
        (folder / "tailoring.json").write_text(json.dumps(app.tailoring, indent=2, ensure_ascii=False))
        print(digest.caption(app, limit=10_000))
        if app.answers is not None:
            form = answer.render(app.answers, f"#{pid} {app.company}")
            (folder / "answers.md").write_text(form + "\n")
            print(f"\n{form}")
        print(f"  -> {folder}\n")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def send_digest(store: Store, settings: Settings, *, dry_run: bool) -> None:
    batch = store.undigested()
    apps = store.unsent_applications([r.id for r in batch.rows + batch.resurfaced])
    text = digest.render(batch)
    if dry_run or not (settings.telegram_token and settings.telegram_chat_id):
        print(text)
        for app in apps:
            print(f"\n[tailored résumé] {digest.caption(app)}")
            if app.answers is not None:
                print(f"\n{answer.render(app.answers, f'#{app.posting_id} {app.company}')}")
        if not dry_run:
            # Only what was really sent is marked, so these stay for the first real digest
            print("(TRAWL_TELEGRAM_TOKEN / TRAWL_TELEGRAM_CHAT_ID not set: printed, nothing marked as sent)",
                  file=sys.stderr)
        return
    digest.send_telegram(text, settings.telegram_token, settings.telegram_chat_id)
    store.mark_digested(batch)
    for app in apps:  # a résumé that fails to send goes out with the next digest
        name = f"resume-{_slug(app.company)}-{app.posting_id}.pdf"
        try:
            digest.send_document(name, app.resume_pdf, digest.caption(app), settings.telegram_token,
                                 settings.telegram_chat_id)
            if app.answers is not None:
                digest.send_telegram(answer.render(app.answers, f"#{app.posting_id} {app.company}"),
                                     settings.telegram_token, settings.telegram_chat_id)
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
    sub.add_parser("run", help="collect, process, then digest (what cron runs)")
    a = sub.add_parser("apply", help="fill a form in Chrome; submit once you approve")
    a.add_argument("id", type=int, help="the posting id (#id in the digest)")
    a.add_argument("--no-submit", action="store_true", help="fill the form to look at; never submit")
    a.add_argument("--log", action="store_true", help="show this application's audit log instead")
    ap = sub.add_parser("app", help="open the desktop app (on the Mac)")
    ap.add_argument("--no-window", action="store_true", help="serve the page for a browser instead")
    ap.add_argument("--port", type=int, default=0)
    pr = sub.add_parser("profile", help="the encrypted answers that fill application forms")
    pr.add_argument("action", choices=["init", "edit", "show"],
                    help="init: make a key for .env; edit: open it in $EDITOR; show: which fields are set")
    args = parser.parse_args(argv)

    if args.command == "app":
        from .desktop.__main__ import main as app_main

        app_main(["--port", str(args.port)] + (["--no-window"] if args.no_window else []))
        return
    load_dotenv()
    settings = Settings.from_env()
    if args.command == "profile":
        profile_command(args.action, settings)
        return
    store = Store.connect(settings.database_url)
    if args.command == "apply":
        apply_command(store, settings, args)
        return
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
            tailor_ids(store, settings, http, args.ids, args.out, again=args.again)
        elif args.command == "tailor" and (loaded := load_tailoring(settings)):
            done, _ = tailor_new(store, new_client(), *loaded, min_score=settings.tailor_min_score,
                                 limit=settings.tailor_limit, http=http, profile=load_profile(settings))
            print(f"prepared {done} application{'s' if done != 1 else ''}")
    if args.command in ("digest", "run"):
        send_digest(store, settings, dry_run=getattr(args, "dry_run", False))
    if args.command == "gaps":
        text = digest.render_gaps(store.gap_counts())
        if args.send and settings.telegram_token and settings.telegram_chat_id:
            digest.send_telegram(text, settings.telegram_token, settings.telegram_chat_id)
        else:
            print(text)


def apply_command(store: Store, settings: Settings, args: argparse.Namespace) -> None:
    from .apply import ApplyError, run

    if args.log:
        for e in store.audit(args.id):
            detail = json.dumps(e["detail"], ensure_ascii=False) if e["detail"] else ""
            print(f"{e['at'].astimezone():%Y-%m-%d %H:%M:%S} {e['event']} {detail}")
        return
    try:
        run(store, settings, args.id, load_profile(settings), submit=not args.no_submit)
    except ApplyError as e:
        sys.exit(str(e))
    except ModuleNotFoundError as e:
        if e.name != "playwright":
            raise
        sys.exit('trawl apply needs Playwright: pip install -e ".[apply]", and Chrome '
                 "(or `playwright install chromium`)")


def profile_command(action: str, settings: Settings) -> None:
    path = settings.profile_path
    if action == "init":
        if os.environ.get(profiles.KEY_ENV):
            print(f"{profiles.KEY_ENV} is already set; `trawl profile edit` to fill in the profile")
            return
        print(f"Add this line to .env (here, and on the server), then run `trawl profile edit`:\n\n"
              f"{profiles.KEY_ENV}={profiles.new_key()}")
    elif action == "edit":
        profiles.edit(path)
        print(f"saved, encrypted: {path}")
    else:
        profile = profiles.load(path)
        if profile is None:
            print(f"no profile at {path}")
            return
        for name, v in profile.model_dump().items():  # names only: values stay off the terminal
            print(f"{'✓' if v not in (None, '') else '·'} {name}")
