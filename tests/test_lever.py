"""Lever: reading the apply page's questions, answering citizenship by country, and filling the form
(a stand-in for jobs.lever.co; the browser tests skip without Playwright's browser)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from trawl import fill, forms
from trawl.answer import Replies, Reply, country_answer, resolve
from trawl.profile import Profile
from trawl.submit import Step, plan

FIXTURES = Path(__file__).parent / "fixtures"
PAGE = (FIXTURES / "lever_form.html").read_text()
URL = "https://jobs.lever.co/example/abc-123"
PROFILE = Profile(legal_first_name="Ada", legal_last_name="Lovelace", email="ada@example.com",
                  city="Singapore", university="Nanyang Technological University", citizenship="China",
                  permanent_residency="", agree_recording=True)


def questions() -> list[forms.Question]:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{URL}/apply"
        return httpx.Response(200, text=PAGE)

    return forms.lever(httpx.Client(transport=httpx.MockTransport(handler)), URL)


def test_lever_questions_come_from_labels_and_card_templates() -> None:
    qs = {q.id: q for q in questions()}
    assert [(q.id, q.type, q.required) for q in list(qs.values())[:5]] == [
        ("resume", "file", True), ("name", "text", True), ("email", "text", True),
        ("location", "text", True), ("urls[LinkedIn]", "text", False)]
    assert qs["name"].label == "Full name"
    deadline = qs["cards[c1][field0]"]
    assert (deadline.type, deadline.options, deadline.required) == ("select", ["Yes", "No"], True)
    assert qs["cards[c1][field1]"].type == "multi_select"
    assert qs["cards[c2][field0]"].options == ["Nanyang Polytechnic",
                                               "Nanyang Technological University - NTU"]
    assert qs["cards[c2][field1]"].type == "long_text"
    gender = qs["eeo[gender]"]
    assert gender.demographic and gender.options == ["Male", "Female", "Decline to self-identify"]
    assert "comments" not in qs and "selectedLocation" not in qs


def test_fetch_reads_lever_forms() -> None:
    http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=PAGE)))
    assert len(forms.fetch(http, "lever", "abc-123", {}, URL)) == 12  # 5 standard, 6 in cards, 1 EEO
    assert forms.board_from_url(URL) == "example"


@pytest.mark.parametrize(("label", "countries", "answer"), [
    ("Are you a Singapore citizen?", "China", ["No"]),
    ("Are you a Singapore citizen?", "China, Singapore", ["Yes"]),
    ("Are you a Singapore permanent resident?", "", ["No"]),  # none
    ("Are you a citizen of China?", "china", ["Yes"]),
    ("Are you an Indian citizen?", "India", ["No"]),  # "India" isn't "Indian": a word, not a prefix
])
def test_country_answer(label: str, countries: str, answer: list[str]) -> None:
    assert country_answer(["Yes", "No"], label, countries) == answer


def test_citizenship_questions_are_answered_by_code() -> None:
    qs = [q for q in questions() if q.label.startswith("Are you a Singapore citizen")]
    replies = Replies(replies=[Reply(id="q1", kind="profile", profile_key="citizenship")])
    answers = resolve(qs, replies, PROFILE, check_against="")
    assert (answers[0].status, answers[0].value) == ("filled", ["No"])
    no_profile = resolve(qs, replies, Profile(), check_against="")
    assert no_profile[0].status == "needs_you"


def ans(q: forms.Question, status: str, value=None, source: str = "", note: str = "") -> dict:  # noqa: ANN001
    return {"question": q.to_dict(), "status": status, "value": value, "source": source, "note": note}


def drafted() -> list[dict]:
    qs = {q.id: q for q in questions()}
    return [
        ans(qs["resume"], "filled", "tailored résumé PDF", "resume"),
        ans(qs["name"], "filled", None, "full_name"),
        ans(qs["email"], "filled", None, "email"),
        ans(qs["location"], "filled", None, "city"),
        ans(qs["cards[c1][field0]"], "filled", ["No"], "offer_deadline"),
        ans(qs["cards[c1][field1]"], "filled", ["English (ENG)", "Mandarin (MAN)"], "languages"),
        ans(qs["cards[c2][field0]"], "filled", ["Nanyang Technological University - NTU"], "university"),
        ans(qs["cards[c2][field1]"], "drafted", "The deployment work.", "draft"),
        ans(qs["cards[c3][field0]"], "filled", ["No"], "citizenship"),
        ans(qs["cards[c3][field1]"], "needs_you", note="a consent: yours to give"),
        ans(qs["eeo[gender]"], "filled", ["Decline to self-identify"], "declined"),
    ]


def test_lever_plan_chooses_by_name() -> None:
    pl = plan(drafted(), PROFILE, "lever")
    steps = {s.field: s for s in pl.steps}
    assert steps["cards[c1][field1]"] == Step("cards[c1][field1]", "Language Skill(s) (Check all that apply)",
                                              "named", ["English (ENG)", "Mandarin (MAN)"])
    assert steps["name"] == Step("name", "Full name", "text", "Ada Lovelace", secret=True)
    assert steps["cards[c3][field1]"].value == ["Yes, I consent"]  # the profile agrees to AI notetakers
    assert not any(s.optional for s in pl.steps)  # none of Greenhouse's page-only fields
    assert pl.left == []


@pytest.fixture
def page():  # noqa: ANN201
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no browser for Playwright: {e}")
        pg = browser.new_page()
        pg.set_content(PAGE)
        yield pg
        browser.close()


def test_fill_and_submit_a_lever_form(page, tmp_path: Path) -> None:  # noqa: ANN001
    resume = tmp_path / "Ada Lovelace Resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n")
    filled = fill.fill(page, plan(drafted(), PROFILE, "lever").steps, resume)
    assert filled.failed == []
    value = page.locator  # a short name for reading the page back
    assert value('[name="name"]').input_value() == "Ada Lovelace"
    assert value('[name="cards[c1][field0]"][value="No"]').is_checked()
    assert value('[name="cards[c1][field1]"][value="Mandarin (MAN)"]').is_checked()
    assert not value('[name="cards[c1][field1]"][value="Other"]').is_checked()
    assert value('[name="cards[c2][field0]"]').input_value() == "Nanyang Technological University - NTU"
    assert value('[name="eeo[gender]"]').input_value() == "Decline to self-identify"
    # the location was picked from the suggestions, not just typed: the exact one, not "Singapore, SGP"
    assert value("#location-input").input_value() == "Singapore"
    assert value("#selected-location").input_value() == '{"name":"Singapore"}'
    assert value("#resume-upload-input").evaluate("e => e.files[0].name") == "Ada Lovelace Resume.pdf"

    assert fill.empty_required(page) == []
    assert fill.submit(page, fill.BOARDS["lever"], wait_s=3).outcome == "confirmed"


def test_empty_required_sees_fields_known_only_by_name(page) -> None:  # noqa: ANN001
    empty = fill.empty_required(page)
    assert "Full name" in empty and "Current location" in empty  # labelled, by name
    assert {"cards[c2][field0]", "cards[c2][field1]"} <= set(empty)  # a select and a textarea, by name
    page.locator('[name="cards[c2][field0]"]').select_option(label="Nanyang Polytechnic")
    assert "cards[c2][field0]" not in fill.empty_required(page)


def test_a_lever_form_missing_a_choice_is_refused(page) -> None:  # noqa: ANN001
    got = fill.submit(page, fill.BOARDS["lever"], wait_s=3)
    assert (got.outcome, got.errors) == ("errors", ["Please complete the required fields"])


def test_a_human_check_in_view_waits_for_the_person() -> None:
    sig = {"confirmation_url": False, "form": True, "thanks": False, "code_input": False, "code_text": False,
           "errors": [], "invalid": []}
    assert fill.outcome(sig, sig | {"challenge": True}).outcome == "challenge"
    assert fill.outcome(sig, sig | {"confirmation_url": True, "challenge": True}).outcome == "confirmed"


# --- answers decided by code: years, where you heard, languages, citizenship -------------------------

from trawl.answer import choose, pick  # noqa: E402
from trawl.forms import Question  # noqa: E402
from trawl.submit import refill  # noqa: E402

HEARD = ["Agency or Non-Palantir Recruiter", "Glassdoor", "LinkedIn", "Palantir Recruiter",
         "Palantir Website", "University Job Board", "Other"]


def test_profile_values_meet_options() -> None:
    assert pick(["2026", "2027", "Other"], "2027-06") == ["2027"]  # options that are only years
    assert pick(["May - July 2027", "2027"], "2027-06") == ["May - July 2027"]  # a range still wins
    assert choose(HEARD, "how_heard", "Company website", "How did you hear?") == ["Palantir Website"]
    assert choose(["LinkedIn", "Other"], "how_heard", "Company website", "How did you hear?") == []
    langs = ["English (ENG)", "Spanish (SPA)", "Mandarin (MAN)", "Other"]
    assert choose(langs, "languages", "English, Mandarin", "Languages", multi=True) == ["English (ENG)",
                                                                                         "Mandarin (MAN)"]


def test_citizenship_doesnt_depend_on_the_model() -> None:
    q = Question("c", "Are you a Singapore citizen?", True, "select", ["Yes", "No"])
    [a] = resolve([q], Replies(replies=[Reply(id="q1", kind="unknown")]), PROFILE, check_against="")
    assert (a.status, a.value, a.source) == ("filled", ["No"], "citizenship")
    # and in a form drafted before the profile knew
    waiting = {"question": q.to_dict(), "status": "needs_you", "value": None, "source": "",
               "note": "trawl doesn't know how to answer this"}
    assert refill(waiting, PROFILE)["value"] == ["No"]


def test_the_resume_answers_only_a_file_question() -> None:
    q = Question("l", "Language Skill(s)", True, "multi_select", ["English (ENG)"])
    [a] = resolve([q], Replies(replies=[Reply(id="q1", kind="profile", profile_key="resume")]), PROFILE,
                  check_against="")
    assert a.status == "needs_you"


def test_a_year_that_had_no_option_is_answered_now() -> None:
    q = Question("y", "Intended graduation year", True, "select", ["2026", "2027", "Other"])
    waiting = {"question": q.to_dict(), "status": "needs_you", "value": None, "source": "",
               "note": "no option matches your graduation"}
    assert refill(waiting, PROFILE.model_copy(update={"graduation": "2027-06"}))["value"] == ["2027"]


def test_high_school_is_never_a_university() -> None:
    name = Question("h", "High School Name", True, "long_text")
    year = Question("y", "Year of High School Graduation", True, "select", ["2019", "2020", "2021"])
    replies = Replies(replies=[Reply(id="q1", kind="profile", profile_key="previous_university"),
                               Reply(id="q2", kind="unknown")])
    prof = PROFILE.model_copy(update={"previous_university": "Wuhan University"})
    a, b = resolve([name, year], replies, prof, check_against="")
    assert (a.status, a.note) == ("needs_you", "not in your profile: high_school")
    assert (b.status, b.note) == ("needs_you", "not in your profile: high_school_graduation")
    prof = prof.model_copy(update={"high_school": "Example High School", "high_school_graduation": "2020-06"})
    a, b = resolve([name, year], replies, prof, check_against="")
    assert (a.status, a.source, b.value) == ("filled", "high_school", ["2020"])


def test_a_draft_that_used_a_university_for_high_school_waits_again() -> None:
    q = Question("h", "High School Name", True, "long_text")
    wrong = {"question": q.to_dict(), "status": "filled", "value": None, "source": "previous_university",
             "note": ""}
    prof = PROFILE.model_copy(update={"previous_university": "Wuhan University"})
    assert plan([wrong], prof, "lever").left == ["High School Name (not in your profile: high_school)"]
    steps = plan([wrong], prof.model_copy(update={"high_school": "Example High School"}), "lever").steps
    assert [(s.field, s.value) for s in steps] == [("h", "Example High School")]
