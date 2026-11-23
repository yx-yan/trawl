"""Fill a Greenhouse or Ashby application form in a real browser, and submit it once the person says so.

This runs on the person's own machine, in a visible Chrome window, so they can watch, finish what
trawl leaves (consents, files, questions only they can answer) and solve any challenge the page
puts up: trawl never tries to get around one. Playwright is an optional dependency
(`pip install -e ".[apply]"`), so the server that collects jobs doesn't need a browser.

Both boards give each field the id their API names it by.
- Greenhouse (job-boards.greenhouse.io): selects are react-select comboboxes whose options appear
  in a listbox the input points to with aria-controls; multi-selects are checkboxes `question_x[]`.
- Ashby (jobs.ashbyhq.com): selects are radio buttons and multi-selects checkboxes, each with an id
  `<form>_<field>-labeled-radio-<n>` and a label holding the option; a required field's title label
  has a `_required_` class. Its page has other `type=submit` buttons (résumé uploads) before the
  real one, so each board names its Submit button.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .submit import Step, match_option

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.sync_api import Page

OPTIONS_TIMEOUT_MS = 6000
NO_MATCH = "no single option matches: pick it yourself"


@dataclass(frozen=True)
class Board:
    url: str  # the form page, from the board's name and the job's id
    submit: str  # the Submit button


BOARDS = {
    "greenhouse": Board("https://job-boards.greenhouse.io/embed/job_app?for={board}&token={job}",
                        '#application-form button[type="submit"]'),
    "ashby": Board("https://jobs.ashbyhq.com/{board}/{job}/application",
                   "button.ashby-application-form-submit-button"),
}


def form_url(source: str, board: str, job_id: str) -> str:
    return BOARDS[source].url.format(board=board, job=job_id)


@dataclass
class Filled:
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)  # "label (why)": left for the person


def fill(page: Page, steps: list[Step], resume: Path) -> Filled:
    out = Filled()
    for s in steps:
        el = page.locator(f'[id="{s.field}"]') if s.kind not in ("check", "pick") else None
        if el is not None and el.count() == 0:
            if not s.optional:
                out.failed.append(f"{s.label} (not on the page)")
            continue
        try:
            why = _step(page, s, resume)
        except Exception as e:  # noqa: BLE001 - a page that behaves oddly leaves the field to the person
            why = f"{type(e).__name__}: {str(e).splitlines()[0][:80]}"
        if why:
            out.failed.append(f"{s.label} ({why})")
        else:
            out.done.append(s.label)
    return out


def _step(page: Page, s: Step, resume: Path) -> str | None:
    """Do one step; the reason it couldn't, or None."""
    if s.kind == "text":
        el = page.locator(f'[id="{s.field}"]')
        if el.get_attribute("role") == "combobox":  # Ashby's location: a picker that looks like text
            return _choose(page, s.field, str(s.value))
        el.fill(str(s.value))
    elif s.kind == "file":
        page.locator(f'[id="{s.field}"]').set_input_files(str(resume))
    elif s.kind == "check":
        boxes = page.locator(f'input[type="checkbox"][name="{s.field}"]')
        labels = [boxes.nth(i).evaluate("e => e.labels && e.labels[0] ? e.labels[0].innerText : ''")
                  for i in range(boxes.count())]
        for want in s.value:
            i = match_option(labels, want)
            if i is None:
                return "no box matches: tick it yourself"
            boxes.nth(i).check()
    elif s.kind == "pick":  # Ashby: radio buttons or checkboxes, one labelled input per option
        inputs = page.locator(f'input[id*="_{s.field}-labeled-"]')
        if inputs.count() == 0:
            return "not on the page"
        labels = [inputs.nth(i).evaluate("e => e.labels && e.labels[0] ? e.labels[0].innerText : ''")
                  for i in range(inputs.count())]
        for want in s.value:
            i = match_option(labels, want)
            if i is None:
                return NO_MATCH
            inputs.nth(i).check()
    else:
        for want in s.value:
            if why := _choose(page, s.field, want):
                return why
    return None


def _choose(page: Page, field_id: str, want: str) -> str | None:
    el = page.locator(f'[id="{field_id}"]')
    el.click()
    el.fill(want[:40])  # react-select filters as you type
    listbox = el.get_attribute("aria-controls")
    opts = (page.locator(f'[id="{listbox}"] [role="option"]') if listbox
            else page.locator('[role="option"]:visible'))
    try:
        opts.first.wait_for(state="visible", timeout=OPTIONS_TIMEOUT_MS)
    except Exception:  # noqa: BLE001 - playwright's TimeoutError; no options for this text
        _clear(page, el)
        return NO_MATCH  # never the value: it may be from the profile
    page.wait_for_timeout(400)  # options that load from a search (schools, cities) settle
    i = match_option(opts.all_inner_texts(), want)
    if i is None:
        _clear(page, el)
        return NO_MATCH
    opts.nth(i).click()
    return None


def _clear(page: Page, el) -> None:  # noqa: ANN001 - a playwright Locator
    el.fill("")
    page.keyboard.press("Escape")


# Required fields with nothing in them. react-select keeps its input empty after a choice, so a
# combobox counts as filled when its control shows a chosen value; its own hidden "required" input
# (no id) is skipped. A required checkbox group needs one box ticked.
EMPTY_REQUIRED_JS = """() => {
  const form = document.querySelector('#application-form') || document;
  const label = e => {
    const set = e.closest('fieldset');
    const text = (set && set.querySelector('legend')) ? set.querySelector('legend').innerText
               : (e.labels && e.labels[0]) ? e.labels[0].innerText
               : (e.getAttribute('aria-label') || e.id || e.name);
    return text.replace(/\\s+/g, ' ').replace(/\\*$/, '').trim();
  };
  const out = [], groups = new Set();
  for (const e of form.querySelectorAll('input, textarea')) {
    if (['hidden', 'search', 'file', 'submit', 'button'].includes(e.type)) continue;
    if (!(e.required || e.getAttribute('aria-required') === 'true')) continue;
    if (e.type === 'checkbox' || e.type === 'radio') {
      if (e.name && groups.has(e.name)) continue;
      groups.add(e.name);
      const all = e.name ? form.querySelectorAll(`input[name="${CSS.escape(e.name)}"]`) : [e];
      if (![...all].some(c => c.checked)) out.push(label(e));
    } else if (e.getAttribute('role') === 'combobox') {
      const ctl = e.closest('[class*="control"]');
      if (!(ctl && ctl.querySelector('[class*="single-value"], [class*="multi-value"]'))) out.push(label(e));
    } else if (e.id && !e.value.trim()) {
      out.push(label(e));
    }
  }
  // Ashby marks the title label of a required field; the field is the label's target, or a group
  // of labelled radios / checkboxes
  for (const l of form.querySelectorAll('label[class*="_required_"][for]')) {
    const el = document.getElementById(l.htmlFor);
    const text = l.innerText.replace(/\\s+/g, ' ').trim();
    if (el && el.type === 'file') { if (!el.files.length) out.push(text); continue; }
    if (el && 'value' in el && el.type !== 'checkbox' && el.type !== 'radio') {
      if (!el.value.trim()) out.push(text);
      continue;
    }
    const group = form.querySelectorAll(`input[id*="_${CSS.escape(l.htmlFor)}-labeled-"]`);
    if (group.length && ![...group].some(c => c.checked)) out.push(text);
  }
  return [...new Set(out)];
}"""

# Everything the person is about to approve, to hash: each field and what's in it.
FORM_STATE_JS = """() => {
  const form = document.querySelector('#application-form') || document;
  return [...form.querySelectorAll('input, textarea')]
    .filter(e => (e.id || e.name) && !(e.name || '').startsWith('g-recaptcha')).map(e => {
    let v = e.value;
    if (e.type === 'checkbox' || e.type === 'radio') v = e.checked;
    else if (e.type === 'file') v = [...(e.files || [])].map(f => f.name + ':' + f.size).join(',');
    else if (e.getAttribute('role') === 'combobox') {
      const ctl = e.closest('[class*="control"]');
      const chosen = ctl ? ctl.querySelectorAll('[class*="single-value"], [class*="multi-value"]') : [];
      v = [...chosen].map(x => x.innerText);
    }
    return [e.id || e.name, v];
  });
}"""

# What the page shows, before and after Submit; `outcome` compares the two. The form page can
# carry the job description, whose "thank you for your interest" means nothing while the form is
# still there. Greenhouse may email a security code before it accepts an application; the person
# types that into the page.
SIGNALS_JS = """(submit) => {
  const text = document.body.innerText;
  const button = document.querySelector(submit);
  return {
    confirmation_url: /confirmation/i.test(location.href),
    form: !!(button && button.offsetParent !== null),
    thanks: /thank you for (applying|your (application|interest))/i.test(text)
         || /application (has been |was )?(successfully )?(submitted|received)/i.test(text),
    code_input: !!document.querySelector('input[id*="security" i], input[name*="security" i]'),
    code_text: /security code/i.test(text),
    errors: [...new Set([...document.querySelectorAll('[class*="error"], [role="alert"]')]
      .map(e => e.innerText.trim()).filter(t => t && t.length < 200))].slice(0, 10),
    // the browser's own checks (a required box, an email's format) stop Submit without a message
    invalid: [...document.querySelectorAll('#application-form :invalid')].filter(e => e.validationMessage)
      .map(e => ((e.labels && e.labels[0] ? e.labels[0].innerText : e.id || e.name).replace(/\\*$/, '').trim()
                 + ': ' + e.validationMessage)).slice(0, 10),
  };
}"""

Outcome = Literal["confirmed", "code", "errors", "pending", "unknown"]


@dataclass
class Result:
    outcome: Outcome
    errors: list[str] = field(default_factory=list)


def outcome(before: dict, now: dict) -> Result:
    """Where an application stands after Submit, from what changed on the page."""
    if now["confirmation_url"] or (now["thanks"] and not now["form"]):
        return Result("confirmed")
    if now["code_input"] or (now["code_text"] and not before["code_text"]):
        return Result("code")
    new = [e for e in now["errors"] if e not in before["errors"]]
    if new or now.get("invalid"):
        return Result("errors", new + now.get("invalid", []))
    return Result("pending")


def empty_required(page: Page) -> list[str]:
    return page.evaluate(EMPTY_REQUIRED_JS)


def form_state(page: Page) -> list:
    return page.evaluate(FORM_STATE_JS)


def signals(page: Page, board: Board) -> dict:
    return page.evaluate(SIGNALS_JS, board.submit)


def submit(page: Page, board: Board, wait_s: float = 20) -> Result:
    before = signals(page, board)
    page.locator(board.submit).first.click()
    return wait_outcome(page, board, before, wait_s)


def wait_outcome(page: Page, board: Board, before: dict, wait_s: float, *,
                 until: tuple[str, ...] = ("confirmed", "code", "errors")) -> Result:
    deadline = time.monotonic() + wait_s
    result = Result("pending")
    while time.monotonic() < deadline:
        try:
            result = outcome(before, signals(page, board))
        except Exception:  # noqa: BLE001 - the page is navigating
            result = Result("pending")
        if result.outcome in until:
            return result
        page.wait_for_timeout(500)
    return result if result.outcome != "pending" else Result("unknown")


def launch(playwright, profile_dir: Path):  # noqa: ANN001, ANN201 - playwright types are optional
    """A visible Chrome with its own profile in ~/.trawl/browser, kept between runs. Playwright's
    bundled Chromium if Chrome isn't installed."""
    profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        return playwright.chromium.launch_persistent_context(str(profile_dir), channel="chrome",
                                                             headless=False, viewport=None)
    except Exception:  # noqa: BLE001 - no Chrome: use Playwright's own
        return playwright.chromium.launch_persistent_context(str(profile_dir), headless=False, viewport=None)
