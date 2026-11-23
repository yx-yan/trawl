"""M3: the fill plan, reading the page after Submit, the checks before a browser opens, and the
filler itself against a stand-in for Greenhouse's form (skipped without Playwright's browser)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from trawl import fill
from trawl.apply import ApplyError, resume_name, run
from trawl.config import Settings
from trawl.profile import Profile
from trawl.submit import Step, degree_level, dom_id, match_option, plan

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile(legal_first_name="Ada", legal_last_name="Lovelace", email="ada@example.com",
                  phone="+65 8000 0000", city="Singapore", country="Singapore",
                  university="Nanyang Technological University")


def ans(qid: str, label: str, status: str, value=None, source: str = "", note: str = "",  # noqa: ANN001
        type_: str = "text", options: list[str] | None = None) -> dict:
    return {"question": {"id": qid, "label": label, "required": True, "type": type_, "options": options or [],
                         "demographic": False, "description": ""},
            "status": status, "value": value, "source": source, "note": note}


ANSWERS = [
    ans("first_name", "First Name", "filled", None, "legal_first_name"),
    ans("email", "Email", "filled", None, "email"),
    ans("resume", "Resume/CV", "filled", "tailored résumé PDF", "resume", type_="file"),
    ans("question_1", "Eligible to work?", "filled", ["Yes, I am eligible"], "authorized_to_work",
        type_="select"),
    ans("question_2[]", "When can you start?", "filled", ["Q3 2027 (July - September)"], "start_date",
        type_="multi_select"),
    ans("question_3", "Why us?", "drafted", "Because of the work.", "draft", type_="long_text"),
    ans("question_4", "I confirm this is true", "needs_you", note="a consent: yours to give",
        type_="boolean"),
    ans("question_5", "Salary?", "filled", None, "salary_expectation"),
    ans("location", "Location", "filled", None, "city"),
    ans("demographic_591", "Gender", "filled", ["Decline to Self Identify"], "declined", type_="select"),
    ans("question_6", "Pronouns", "skipped"),
]


def test_plan_fills_from_the_profile_and_leaves_the_rest() -> None:
    pl = plan(ANSWERS, PROFILE)
    steps = {s.field: s for s in pl.steps}
    assert steps["first_name"] == Step("first_name", "First Name", "text", "Ada", secret=True)
    assert steps["resume"].kind == "file"
    assert steps["question_1"].kind == "choose" and not steps["question_1"].secret
    assert steps["question_2[]"].kind == "check"
    assert steps["question_3"].value == "Because of the work."
    # the API's location question is the page's city picker; demographic_591 is #591
    assert steps["candidate-location"] == Step("candidate-location", "Location", "choose", ["Singapore"],
                                               secret=True)
    assert steps["591"].value == ["Decline to Self Identify"]
    # page-only fields from the profile, each only if the page has it; no duplicate city picker
    assert [s.field for s in pl.steps if s.optional] == ["country", "school--0"]
    assert "question_6" not in steps
    assert pl.left == ["I confirm this is true (a consent: yours to give)",
                       "Salary? (not in your profile: salary_expectation)"]


def test_plan_answers_what_waited_for_the_profile_once_it_has_it() -> None:
    waiting = [
        ans("question_7", "Current employer?", "needs_you", note="not in your profile: current_employer"),
        ans("question_8", "How did you hear?", "needs_you", note="not in your profile: how_heard",
            type_="select", options=["LinkedIn", "Company website"]),
        ans("question_9", "GPA?", "needs_you", note="not in your profile: gpa"),
        ans("question_4", "I confirm this is true", "needs_you", note="a consent: yours to give"),
    ]
    prof = PROFILE.model_copy(update={"current_employer": "Example Co", "how_heard": "Company website"})
    pl = plan(waiting, prof)
    steps = {s.field: s for s in pl.steps}
    assert steps["question_7"] == Step("question_7", "Current employer?", "text", "Example Co", secret=True)
    assert steps["question_8"].value == ["Company website"]
    # still missing, and consents are never the profile's
    assert pl.left == ["GPA? (not in your profile: gpa)", "I confirm this is true (a consent: yours to give)"]


EARLIER = PROFILE.model_copy(update={"previous_university": "Wuhan University",
                                     "previous_degree": "Bachelor of Engineering",
                                     "previous_major": "Energy Storage Science and Engineering"})


def test_plan_adds_an_earlier_degree_behind_add_another() -> None:
    steps = {s.field: s for s in plan([], EARLIER).steps}
    assert steps["school--1"] == Step("school--1", "Earlier school", "choose", ["Wuhan University"],
                                      optional=True, opens="Add another")
    # only the school opens the entry: a field the page lacks (discipline) must not add another one
    assert steps["degree--1"].value == ["Bachelor's Degree"] and not steps["degree--1"].opens
    assert not steps["discipline--1"].opens
    assert "school--1" not in {s.field for s in plan([], PROFILE).steps}


def test_plan_log_and_shown_never_carry_profile_values() -> None:
    pl = plan(ANSWERS, PROFILE)
    text = repr(pl.log()) + " ".join(s.shown() for s in pl.steps)
    for secret in ("Ada", "ada@example.com", "8000", "Singapore"):
        assert secret not in text


def test_plan_without_a_profile_leaves_profile_fields_to_you() -> None:
    pl = plan(ANSWERS, None)
    assert "first_name" not in {s.field for s in pl.steps}
    assert "First Name (not in your profile: legal_first_name)" in pl.left
    assert not any(s.optional for s in pl.steps)


def test_dom_id() -> None:
    assert dom_id("demographic_591") == "591"
    assert dom_id("location") == "candidate-location"
    assert dom_id("question_68860629[]") == "question_68860629[]"


@pytest.mark.parametrize(("degree", "level"), [
    ("Master of Science (Signal Processing)", "Master's Degree"),
    ("M.Sc. Computer Science", "Master's Degree"),
    ("MEng", "Master's Degree"),
    ("Diploma in Engineering", "Diploma in Engineering"),  # unknown: stays, and the person picks
    ("B.Eng. (Electrical and Electronic Engineering)", "Bachelor's Degree"),
    ("Bachelor of Engineering", "Bachelor's Degree"),
    ("MBA", "Master of Business Administration (M.B.A.)"),
    ("PhD in Physics", "Doctor of Philosophy (Ph.D.)"),
])
def test_degree_level(degree: str, level: str) -> None:
    assert degree_level(degree) == level


def test_plan_picks_the_degree_by_level() -> None:
    pl = plan([], PROFILE.model_copy(update={"degree": "Master of Science"}))
    assert next(s for s in pl.steps if s.field == "degree--0").value == ["Master's Degree"]


def test_match_option_never_guesses_between_several() -> None:
    schools = ["Nanyang Polytechnic (NYP)", "Nanyang Technological University (NTU)"]
    assert match_option(schools, "Nanyang Technological University") == 1
    assert match_option(schools, "Nanyang") is None
    assert match_option(["Singapore", "Singapore, Maharashtra, India"], "Singapore") == 0
    assert match_option(["Singapore +65"], "singapore") == 0
    assert match_option(["Yes", "No"], "Maybe") is None


SIGNALS = {"confirmation_url": False, "form": True, "thanks": False, "code_input": False, "code_text": False,
           "errors": [], "invalid": []}


def sig(**kw: object) -> dict:
    return SIGNALS | kw


def test_outcome_reads_what_changed_after_submit() -> None:
    # the job description's "thank you for your interest" means nothing while the form is there
    before = sig(thanks=True)
    assert fill.outcome(before, sig(thanks=True)).outcome == "pending"
    assert fill.outcome(before, sig(thanks=True, form=False)).outcome == "confirmed"
    assert fill.outcome(before, sig(confirmation_url=True)).outcome == "confirmed"
    assert fill.outcome(sig(), sig(code_text=True)).outcome == "code"
    assert fill.outcome(sig(code_text=True), sig(code_text=True)).outcome == "pending"
    assert fill.outcome(sig(), sig(code_input=True)).outcome == "code"
    old = sig(errors=["an old banner"])
    got = fill.outcome(old, sig(errors=["an old banner", "Email is required"]))
    assert (got.outcome, got.errors) == ("errors", ["Email is required"])
    # the browser's own checks alone prove nothing: Greenhouse submits from script, past them
    assert fill.outcome(sig(), sig(invalid=["Email: Please include an '@'"])).outcome == "pending"


def test_resume_name() -> None:
    assert resume_name(PROFILE) == "Ada Lovelace Resume.pdf"
    assert resume_name(None) == "Resume.pdf"


# --- the checks before a browser opens -----------------------------------------------------------


@dataclass
class FakePosting:
    source: str = "greenhouse"
    facts: dict = field(default_factory=lambda: {"board": "example"})
    company: str = "Example"
    title: str = "FDE"
    url: str = "https://example.com/job"
    external_id: str = "1"


@dataclass
class FakeApp:
    answers: list | None = field(default_factory=list)
    source_hash: str = "h"
    resume_pdf: bytes = b"%PDF"


@dataclass
class FakeStore:
    p: FakePosting | None = field(default_factory=FakePosting)
    app: FakeApp | None = field(default_factory=FakeApp)
    done: bool = False
    today: int = 0

    def posting(self, _: int) -> FakePosting | None:
        return self.p

    def submitted(self, _: int) -> bool:
        return self.done

    def latest_application(self, _: int) -> FakeApp | None:
        return self.app

    def submitted_since(self, _: int) -> int:
        return self.today


@pytest.mark.parametrize(("store", "message"), [
    (FakeStore(p=None), "no such posting"),
    (FakeStore(p=FakePosting(source="lever")), "Greenhouse and Ashby forms only"),
    (FakeStore(done=True), "already submitted"),
    (FakeStore(app=None), "trawl tailor 7"),
    (FakeStore(app=FakeApp(answers=None)), "trawl tailor 7"),
    (FakeStore(today=5), "limit"),
])
def test_apply_refuses_before_opening_a_browser(store: FakeStore, message: str) -> None:
    with pytest.raises(ApplyError, match=message):
        run(store, Settings(), 7, PROFILE)  # type: ignore[arg-type]


def test_the_daily_limit_doesnt_stop_filling_without_submitting(monkeypatch: pytest.MonkeyPatch) -> None:
    class Opened(Exception):
        pass

    def no_browser(*_: object, **__: object) -> None:
        raise Opened

    monkeypatch.setattr("trawl.apply.plan", no_browser)
    with pytest.raises(Opened):
        run(FakeStore(today=5), Settings(), 7, PROFILE, submit=False)  # type: ignore[arg-type]


# --- the filler, in a browser, against a stand-in form -------------------------------------------


@pytest.fixture
def page():  # noqa: ANN201
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no browser for Playwright: {e}")
        pg = browser.new_page()
        pg.set_content((FIXTURES / "greenhouse_form.html").read_text())
        yield pg
        browser.close()


def test_fill_then_submit_once_the_person_has_consented(page, tmp_path: Path) -> None:  # noqa: ANN001
    resume = tmp_path / "Resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    steps = plan(ANSWERS, PROFILE).steps
    filled = fill.fill(page, steps, resume)
    # a question the page lacks is left to you; a page-only extra it lacks (#country) is not asked for
    assert filled.failed == ["Location (not on the page)", "Gender (not on the page)"]
    assert page.locator("#first_name").input_value() == "Ada"
    assert page.locator("#question_3").input_value() == "Because of the work."
    assert page.locator("#q2_b").is_checked() and not page.locator("#q2_a").is_checked()
    # the school is NTU, never the first option "Nanyang" also matches
    chosen = page.locator(".select__single-value").all_inner_texts()
    assert chosen == ["Yes, I am eligible", "Nanyang Technological University (NTU)"]
    assert page.locator("#resume").evaluate("e => e.files[0].name") == "Resume.pdf"

    # the consent is the person's; until it's ticked the form is incomplete, and the page refuses it
    assert fill.empty_required(page) == ["I consent to Example processing my data"]
    state = fill.form_state(page)
    assert ["question_1", ["Yes, I am eligible"]] in state
    got = fill.submit(page, fill.BOARDS["greenhouse"], wait_s=3)
    assert (got.outcome, got.errors) == ("errors", ["Please consent to continue"])

    page.locator("#consent").check()
    assert fill.empty_required(page) == []
    # the browser's own check stops Submit without a message on the page; trawl still says why
    page.locator("#email").fill("not an email")
    got = fill.submit(page, fill.BOARDS["greenhouse"], wait_s=3)
    assert got.outcome == "errors" and got.errors[0].startswith("Email: ")
    page.locator("#email").fill("ada@example.com")
    assert fill.submit(page, fill.BOARDS["greenhouse"], wait_s=3).outcome == "confirmed"


def test_fill_clicks_add_another_for_the_earlier_degree(page, tmp_path: Path) -> None:  # noqa: ANN001
    steps = [s for s in plan([], EARLIER).steps if s.field.endswith("--1")]
    assert fill.fill(page, steps, tmp_path / "x.pdf").failed == []
    chosen = page.locator("#education .select__single-value").all_inner_texts()
    assert chosen == ["Wuhan University", "Bachelor's Degree"]  # never "Wuhan University of Technology"
    # clicked once: the page has no discipline field, and that mustn't add a third entry
    assert page.locator('[id="school--1"]').count() == 1
    assert page.locator('#education .select__control').count() == 2


def test_a_choice_no_option_matches_is_left_to_you(page, tmp_path: Path) -> None:  # noqa: ANN001
    filled = fill.fill(page, [Step("school--0", "School", "choose", ["Nanyang"]),
                              Step("question_1", "Eligible", "choose", ["Maybe"])], tmp_path / "x.pdf")
    assert filled.failed == [f"School ({fill.NO_MATCH})", f"Eligible ({fill.NO_MATCH})"]
    assert page.locator(".select__single-value").count() == 0


# --- the store: submission state and the audit log (needs TRAWL_TEST_DATABASE_URL) ----------------


def test_store_keeps_submission_and_an_audit_log(store) -> None:  # noqa: ANN001
    from trawl.models import Posting

    store.upsert([Posting("greenhouse", "1", "Example", "FDE", "https://example.com/1", "Singapore", None)])
    pid = store.conn.execute("SELECT id FROM postings").fetchone()["id"]
    store.save_application(pid, "old", {}, "tex", b"%PDF old", [])
    store.save_application(pid, "new", {}, "tex", b"%PDF new", [])
    app = store.latest_application(pid)
    assert (app.source_hash, app.status, app.resume_pdf) == ("new", "drafted", b"%PDF new")
    assert not store.submitted(pid) and store.submitted_since(24) == 0

    store.log(pid, "approved", form_sha256="abc", fields=3)
    store.mark_submitted(pid, "new")
    store.log(pid, "submitted", confirmed_by="page")
    assert store.submitted(pid) and store.submitted_since(24) == 1
    assert store.latest_application(pid).status == "submitted"
    assert [(e["event"], e["detail"]) for e in store.audit(pid)] == [
        ("approved", {"form_sha256": "abc", "fields": 3}), ("submitted", {"confirmed_by": "page"})]


def test_apply_end_to_end_against_the_stand_in_form(store, tmp_path: Path,  # noqa: ANN001
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """`trawl apply` start to finish: plan, fill, refuse while the consent is empty, approve,
    submit, audit. The person's part (ticking the consent) happens between two prompts."""
    pytest.importorskip("playwright.sync_api")
    from trawl import apply
    from trawl.models import Posting

    contexts = []

    def headless(pw, profile_dir: Path):  # noqa: ANN001, ANN202
        try:
            ctx = pw.chromium.launch_persistent_context(str(profile_dir), headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no browser for Playwright: {e}")
        contexts.append(ctx)
        return ctx

    monkeypatch.setattr(fill, "launch", headless)
    monkeypatch.setattr(fill, "form_url", lambda *_: (FIXTURES / "greenhouse_form.html").as_uri())
    monkeypatch.setattr(apply, "HOME", tmp_path)

    store.upsert([Posting("greenhouse", "1", "Example", "FDE", "https://example.com/1", "Singapore", None,
                          facts={"board": "example"})])
    pid = store.conn.execute("SELECT id FROM postings").fetchone()["id"]
    answers = [a for a in ANSWERS if a["question"]["id"] in
               ("first_name", "email", "resume", "question_1", "question_2[]", "question_3", "question_4")]
    store.save_application(pid, "h", {}, "tex", b"%PDF-1.4\n", answers)

    said: list[str] = []
    replies = iter(["submit", "consent", "submit"])

    def ask(_: str) -> str:
        reply = next(replies)
        if reply == "consent":  # the person ticks it in Chrome, then approves
            contexts[0].pages[0].locator("#consent").check()
            return "submit"
        return reply

    got = apply.run(store, Settings(browser_profile=tmp_path / "browser"), pid, PROFILE, ask=ask,
                    say=said.append)
    assert got == "submitted"
    assert any(s.startswith("Still empty: I consent") for s in said)
    assert store.submitted(pid)
    events = [e["event"] for e in store.audit(pid)]
    assert events == ["fill_started", "filled", "approved", "submitted"]
    log = repr(store.audit(pid))
    assert "Ada" not in log and "ada@example.com" not in log
    shots = sorted(p.name.split("-")[0] for p in (tmp_path / "applications").glob("*/*.png"))
    assert shots == ["approved", "submitted"]


# --- Ashby -------------------------------------------------------------------------------------------

ASHBY = [
    ans("_systemfield_name", "Name", "filled", None, "preferred_name"),
    ans("_systemfield_resume", "Resume", "filled", "tailored résumé PDF", "resume", type_="file"),
    ans("auth", "Are you authorized to work in Singapore?", "filled", ["No"], "authorized_to_work",
        type_="select"),
    ans("season", "Graduation season?", "filled", ["May - July 2027"], "graduation", type_="select"),
    ans("why", "Why us?", "drafted", "Because of the work.", "draft", type_="long_text"),
    ans("relocate", "Will you relocate?", "filled", True, "relocate", type_="boolean"),
    ans("consent", "I confirm the information is true", "needs_you", note="a consent: yours to give",
        type_="multi_select"),
]


def test_ashby_plan_picks_labelled_options_and_adds_no_greenhouse_fields() -> None:
    pl = plan(ASHBY, PROFILE.model_copy(update={"preferred_name": "Ada L"}), "ashby")
    steps = {s.field: s for s in pl.steps}
    assert steps["auth"] == Step("auth", "Are you authorized to work in Singapore?", "pick", ["No"])
    assert steps["relocate"].value == ["Yes"]
    assert steps["_systemfield_name"].secret
    assert not any(s.optional for s in pl.steps)  # no phone country, school or degree pickers
    assert pl.left == ["I confirm the information is true (a consent: yours to give)"]


@pytest.fixture
def ashby_page():  # noqa: ANN201
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no browser for Playwright: {e}")
        pg = browser.new_page()
        pg.set_content((FIXTURES / "ashby_form.html").read_text())
        yield pg
        browser.close()


def test_ashby_fill_then_submit_with_the_real_button(ashby_page, tmp_path: Path) -> None:  # noqa: ANN001
    page = ashby_page
    resume = tmp_path / "Resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    steps = plan(ASHBY, PROFILE.model_copy(update={"preferred_name": "Ada L"}), "ashby").steps
    filled = fill.fill(page, steps, resume)
    assert filled.failed == ["Will you relocate? (not on the page)"]
    assert page.locator("#_systemfield_name").input_value() == "Ada L"
    assert page.locator('[id="f1_auth-labeled-radio-1"]').is_checked()
    assert page.locator('[id="f1_season-labeled-radio-1"]').is_checked()
    assert page.locator("#why").input_value() == "Because of the work."

    assert fill.empty_required(page) == ["I confirm the information is true"]
    board = fill.BOARDS["ashby"]
    got = fill.submit(page, board, wait_s=3)
    assert (got.outcome, got.errors) == ("errors", ["Missing entry for required field"])
    page.locator('[id="f1_consent-labeled-checkbox-0"]').check()
    assert fill.empty_required(page) == []
    assert fill.submit(page, board, wait_s=3).outcome == "confirmed"
    assert not page.evaluate("window.wrong === true")  # never an upload button


def test_ashby_empty_required_sees_radios_and_files(ashby_page) -> None:  # noqa: ANN001
    assert fill.empty_required(ashby_page) == ["Name", "Resume", "Are you authorized to work in Singapore?",
                                               "Graduation season?", "I confirm the information is true"]


SCRIPTED = """<form id="application-form">
  <fieldset><legend>Which cohort?</legend>
    <input type="checkbox" name="cohort[]" id="a" required checked><label for="a">January to June</label>
    <input type="checkbox" name="cohort[]" id="b" required><label for="b">June to August</label>
  </fieldset>
  <button type="submit" onclick="event.preventDefault(); setTimeout(() => { document.body.innerHTML =
    '<p>Enter the security code we emailed you</p><input id=security_code>' }, 800)">Submit</button>
</form>"""


def test_a_submit_by_script_is_not_refused_for_the_browsers_own_checks(page) -> None:  # noqa: ANN001
    """Greenhouse marks every box of a checkbox group required and submits from script, past the
    browser's checks: the unticked boxes once read as a refusal while the page was asking for the
    emailed security code."""
    page.set_content(SCRIPTED)
    assert fill.empty_required(page) == []
    assert fill.submit(page, fill.BOARDS["greenhouse"], wait_s=4).outcome == "code"


def _apply_setup(store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, list]:  # noqa: ANN001
    """A Greenhouse posting with a drafted form, and `trawl apply` pointed at the stand-in form in a
    headless browser; returns the posting id and the browser contexts it opens."""
    pytest.importorskip("playwright.sync_api")
    from trawl import apply
    from trawl.models import Posting

    contexts: list = []

    def headless(pw, profile_dir: Path):  # noqa: ANN001, ANN202
        try:
            ctx = pw.chromium.launch_persistent_context(str(profile_dir), headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no browser for Playwright: {e}")
        contexts.append(ctx)
        return ctx

    monkeypatch.setattr(fill, "launch", headless)
    monkeypatch.setattr(fill, "form_url", lambda *_: (FIXTURES / "greenhouse_form.html").as_uri())
    monkeypatch.setattr(apply, "HOME", tmp_path)
    store.upsert([Posting("greenhouse", "1", "Example", "FDE", "https://example.com/1", "Singapore", None,
                          facts={"board": "example"})])
    pid = store.conn.execute("SELECT id FROM postings").fetchone()["id"]
    answers = [a for a in ANSWERS if a["question"]["id"] in ("first_name", "email", "question_1",
                                                             "question_2[]")]
    store.save_application(pid, "h", {}, "tex", b"%PDF-1.4\n", answers)
    return pid, contexts


def test_apply_notices_a_form_submitted_in_the_browser(store, tmp_path: Path,  # noqa: ANN001
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    from trawl import apply

    pid, contexts = _apply_setup(store, tmp_path, monkeypatch)

    def ask(_: str) -> str:  # the person ticks the consent and clicks Submit themselves, then Enter
        page = contexts[0].pages[0]
        page.locator("#consent").check()
        page.locator('button[type="submit"]').click()
        return ""

    assert apply.run(store, Settings(browser_profile=tmp_path / "b"), pid, PROFILE, ask=ask,
                     say=lambda _: None) == "submitted"
    assert store.submitted(pid)
    assert store.audit(pid)[-1]["detail"]["via"] == "the browser"


@pytest.mark.parametrize(("reply", "submitted"), [("y", True), ("", False)])
def test_apply_asks_when_the_browser_is_closed(store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
                                               reply: str, submitted: bool) -> None:
    from trawl import apply

    pid, contexts = _apply_setup(store, tmp_path, monkeypatch)
    replies = iter(["submit", reply])

    def ask(_: str) -> str:
        if contexts[0].pages:
            contexts[0].pages[0].close()  # the person closes the window
        return next(replies)

    got = apply.run(store, Settings(browser_profile=tmp_path / "b"), pid, PROFILE, ask=ask,
                    say=lambda _: None)
    assert (got, store.submitted(pid)) == ("submitted" if submitted else "stopped", submitted)
