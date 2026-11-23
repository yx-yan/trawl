"""`trawl apply <id>`: fill a job's application form in Chrome, and submit it when the person says so.

1. Checks: a Greenhouse or Ashby job, a drafted form (`trawl tailor <id>`), not submitted before, and under
   the daily limit.
2. The plan (`submit.py`) fills what trawl may: drafts, choices, profile fields, the tailored résumé.
   Consents, other files and anything trawl couldn't answer are listed for the person.
3. Chrome opens on the form and trawl fills it. The person finishes and checks it there.
4. Typing `submit` is the approval of that exact form: trawl refuses while a required field is
   empty, hashes what's in every field, screenshots it, then clicks Submit and reads what the page
   says. A security code goes into the page by hand; when trawl can't tell whether it went through,
   it asks.

Every step goes into `audit_log` with field names and counts; the values typed never do, and the
screenshots stay on this machine.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from . import fill
from .config import HOME, Settings
from .forms import board_from_url
from .profile import Profile
from .store import Store
from .submit import CONSENTS, plan


class ApplyError(Exception):
    """Why this application can't be filled now."""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def resume_name(profile: Profile | None) -> str:
    """What the recruiter sees the file called."""
    names = [n for n in (profile.legal_first_name, profile.legal_last_name) if n] if profile else []
    return f"{' '.join(names)} Resume.pdf" if names else "Resume.pdf"


def run(store: Store, settings: Settings, posting_id: int, profile: Profile | None, *, submit: bool = True,
        ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> str:
    """Fill and maybe submit; returns how it ended: submitted, stopped or filled (--no-submit)."""
    p = store.posting(posting_id)
    if p is None:
        raise ApplyError(f"#{posting_id}: no such posting")
    board_name = p.facts.get("board") or board_from_url(p.url)
    if p.source not in fill.BOARDS or not board_name:
        raise ApplyError(f"#{posting_id} is on {p.source}: trawl apply fills Greenhouse, Ashby and Lever "
                         f"forms only. Apply at {p.url}")
    board = fill.BOARDS[p.source]
    if store.submitted(posting_id):
        raise ApplyError(f"#{posting_id} {p.company}: already submitted")
    app = store.latest_application(posting_id)
    if app is None or app.answers is None:
        raise ApplyError(f"#{posting_id}: no drafted form yet: run `trawl tailor {posting_id}` first")
    if submit and (n := store.submitted_since(24)) >= settings.submit_daily_limit:
        raise ApplyError(f"{n} applications submitted in the last 24 hours, the limit "
                         f"(TRAWL_SUBMIT_DAILY_LIMIT={settings.submit_daily_limit})")

    asks_gpa = any(a["question"]["required"] and a.get("note") == "not in your profile: gpa"
                   for a in app.answers)
    if asks_gpa and not (profile and profile.gpa):
        store.log(posting_id, "skipped", reason="requires a GPA")
        raise ApplyError(f"#{posting_id} {p.company}: the form requires a GPA, which your profile leaves "
                         "empty: skipped")
    pl = plan(app.answers, profile, p.source)
    say(f"#{posting_id} {p.company}: {p.title}")
    for s in pl.steps:
        say(f"  ✓ {s.label[:70]} → {s.shown()}")
    store.log(posting_id, "fill_started", source_hash=app.source_hash, **pl.log())

    folder = HOME / "applications" / f"{posting_id}-{_slug(p.company)}"
    from playwright.sync_api import sync_playwright  # optional: pip install -e ".[apply]"

    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        resume = Path(tmp) / resume_name(profile)
        resume.touch(mode=0o600)
        resume.write_bytes(app.resume_pdf)
        ctx = fill.launch(pw, settings.browser_profile)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(fill.form_url(p.source, board_name, p.external_id), wait_until="networkidle")
            filled = fill.fill(page, pl.steps, resume)
            allowed = {kind for kind, key, _ in CONSENTS if profile and getattr(profile, key) is True}
            ticked = fill.tick_consents(page, allowed)
            consents = [c[:80] for c in pl.consents + [text for _, text in ticked]]
            if consents:
                store.log(posting_id, "consented", labels=consents)
                say("Consents given from your profile:\n" + "\n".join(f"  ✓ {c}" for c in consents))
            store.log(posting_id, "filled", done=len(filled.done), failed=filled.failed, left=pl.left)
            todo = pl.left + filled.failed
            # required fields only the page has (a consent box, say), which no answer covered
            todo += [e for e in fill.empty_required(page) if not any(t.startswith(e) for t in todo)]
            say(f"\nFilled {len(filled.done)} fields." + (" Left for you:" if todo else ""))
            for t in todo:
                say(f"  ❗ {t}")
            if not submit:
                ask("\nLook it over in Chrome; nothing will be submitted. Press Enter to close. ")
                store.log(posting_id, "closed", submit=False)
                return "filled"
            return _approve_and_submit(store, posting_id, app.source_hash, page, board, folder, ask, say)
        finally:
            ctx.close()


def _approve_and_submit(store: Store, posting_id: int, source_hash: str, page, board: fill.Board,  # noqa: ANN001
                        folder: Path, ask: Callable[[str], str], say: Callable[[str], None]) -> str:
    while True:
        reply = ask("\nFinish the form in Chrome and check every field. "
                    "Type 'submit' to send it, or press Enter to stop: ").strip().lower()
        # the person may have submitted it in the page, or closed the window, while trawl waited
        if fill.closed(page):
            return _after_close(store, posting_id, source_hash, ask, say)
        if fill.confirmed(fill.signals(page, board)):
            return _submitted(store, posting_id, source_hash, page, folder, say, confirmed_by="page",
                              via="the browser")
        if reply != "submit":
            store.log(posting_id, "stopped")
            say("Stopped; nothing submitted.")
            return "stopped"
        if empty := fill.empty_required(page):
            say("Still empty: " + "; ".join(empty))
            continue
        state = fill.form_state(page)
        digest = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        shot = _screenshot(page, folder, "approved")
        store.log(posting_id, "approved", form_sha256=digest, fields=len(state), screenshot=str(shot))
        result = fill.submit(page, board)
        if result.outcome in ("code", "challenge"):  # the person's part, in the page
            store.log(posting_id, "security_code" if result.outcome == "code" else "challenge")
            say("The board emailed you a security code: type it into the page and submit there. "
                if result.outcome == "code" else
                "The page wants to check you're human: solve it in Chrome (submit there if it asks). ")
            say("Waiting up to 10 minutes.")
            result = fill.wait_outcome(page, board, fill.signals(page, board), 600, until=("confirmed",))
        if result.outcome == "errors":
            store.log(posting_id, "form_errors", errors=result.errors)
            say("The form didn't take it:\n" + "\n".join(f"  • {e}" for e in result.errors))
            continue
        confirmed_by = "page"
        if result.outcome != "confirmed":
            seen = ask("trawl can't tell whether it went through. Does the page say it was submitted? [y/N] ")
            if seen.strip().lower() not in ("y", "yes"):
                store.log(posting_id, "unconfirmed", outcome=result.outcome)
                say("Not marked as submitted. Fix it in Chrome and type 'submit' again, or stop.")
                continue
            confirmed_by = "you"
        return _submitted(store, posting_id, source_hash, page, folder, say, confirmed_by=confirmed_by)


def _submitted(store: Store, posting_id: int, source_hash: str, page, folder: Path,  # noqa: ANN001
               say: Callable[[str], None], *, confirmed_by: str, via: str = "trawl") -> str:
    shot = _screenshot(page, folder, "submitted")
    store.mark_submitted(posting_id, source_hash)
    store.log(posting_id, "submitted", confirmed_by=confirmed_by, via=via, screenshot=str(shot))
    say(f"Submitted{' (in the browser)' if via != 'trawl' else ''}. Screenshots in {folder}")
    return "submitted"


def _after_close(store: Store, posting_id: int, source_hash: str, ask: Callable[[str], str],
                 say: Callable[[str], None]) -> str:
    seen = ask("The browser was closed. Did you submit the application in it, "
               "and did the page say so? [y/N] ")
    if seen.strip().lower() in ("y", "yes"):
        store.mark_submitted(posting_id, source_hash)
        store.log(posting_id, "submitted", confirmed_by="you", via="the browser")
        say("Marked as submitted.")
        return "submitted"
    store.log(posting_id, "browser_closed")
    say("The browser was closed; nothing submitted by trawl.")
    return "stopped"


def _screenshot(page, folder: Path, name: str) -> Path:  # noqa: ANN001
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.png"
    page.screenshot(path=str(path), full_page=True)
    path.chmod(0o600)
    return path
