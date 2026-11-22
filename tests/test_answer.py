from __future__ import annotations

import json

import httpx
import pytest
from conftest import FIXTURES, FakeClient, job_facts

from trawl import answer, forms
from trawl import profile as profiles
from trawl.answer import Replies, Reply, pick, resolve
from trawl.decide import Criteria
from trawl.forms import Question
from trawl.models import Posting
from trawl.process import process, tailor_new
from trawl.profile import Profile
from trawl.tailor import parse

GH = json.loads((FIXTURES / "greenhouse_questions.json").read_text())
ASHBY = json.loads((FIXTURES / "ashby_form.json").read_text())


def form_http() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io":
            assert request.url.params["questions"] == "true"
            return httpx.Response(200, json=GH)
        if request.url.host == "jobs.ashbyhq.com":
            body = json.loads(request.content)
            assert body["variables"] == {"organizationHostedJobsPageName": "Sierra", "jobPostingId": "abc"}
            return httpx.Response(200, json=ASHBY)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


# --- reading forms ---


def test_greenhouse_questions() -> None:
    qs = forms.greenhouse(form_http(), "stripe", "8160776")
    by_label = {q.label: q for q in qs}
    assert by_label["First Name"].id == "first_name" and by_label["First Name"].required
    assert by_label["Resume/CV"].type == "file"  # file first, though a textarea is offered too
    start = by_label["When are you available to start full-time?"]
    assert start.type == "multi_select" and start.options[0] == "Q4 2026 (October - December)"
    assert "Longitude" not in by_label and "Location" in by_label  # hidden inputs dropped
    gender = next(q for q in qs if q.demographic)
    assert gender.options == ["Male", "Female", "Decline to Self Identify"]


def test_ashby_questions_from_the_job_url() -> None:
    qs = forms.fetch(form_http(), "ashby", "abc", {}, "https://jobs.ashbyhq.com/Sierra/abc")
    def by_label(start: str) -> Question:
        return next(x for x in qs if x.label.startswith(start))

    assert by_label("Why are you excited").type == "long_text"
    assert by_label("Are you legally authorized").options == ["Yes", "No"]
    trust = next(q for q in qs if q.label.startswith("Trust is one"))
    assert trust.type == "boolean" and trust.options == []  # a lone unlabelled box is a checkbox
    assert forms.fetch(form_http(), "mcf", "x", {}, "https://mcf") is None


# --- filling them ---


def test_pick_matches_dates_booleans_and_names() -> None:
    grad = ["November - December 2026", "May - July 2027", "November - December 2027"]
    assert pick(grad, "2027-06") == ["May - July 2027"]
    assert pick(["Q2 2027 (April - June)", "Q3 2027 (July - September)"], "2027-07") == [
        "Q3 2027 (July - September)"
    ]
    assert pick(["Summer 2027", "Fall 2027"], "2027-10") == ["Fall 2027"]
    assert pick(["Winter 2027", "Spring 2028"], "2028-01") == []  # winter 2027 ends in 2027's text only
    assert pick(["Yes", "No"], False) == ["No"] and pick(["Yes, I do", "No"], True) == ["Yes, I do"]
    unis = ["Nanyang Technological University (NTU)", "National University of Singapore (NUS)", "Other"]
    assert pick(unis, "Nanyang Technological University") == ["Nanyang Technological University (NTU)"]
    assert pick(unis, "University") == []  # ambiguous: none rather than a guess


def q(
    label: str,
    type_: str = "text",
    required: bool = True,
    options: list[str] | None = None,
    demographic: bool = False,
) -> Question:
    return Question(label.lower().replace(" ", "_"), label, required, type_, options or [], demographic)


ME = Profile(legal_first_name="Ada", email="ada@example.com", needs_sponsorship=True, graduation="2027-06")


def test_resolve_fills_from_the_profile_and_never_stores_text_values() -> None:
    questions = [
        q("First Name"),
        q("Email"),
        q("Phone"),
        q("Sponsorship?", "select", options=["Yes", "No"]),
        q("Graduation", "select", options=["May - July 2027", "Nov 2027"]),
        q("Resume", "file"),
        q("Transcript", "file", required=False),
    ]
    keys = ["legal_first_name", "email", "phone", "needs_sponsorship", "graduation", "resume", "gpa"]
    replies = Replies(
        replies=[Reply(id=f"q{i}", kind="profile", profile_key=k) for i, k in enumerate(keys, 1)]
    )
    a = resolve(questions, replies, ME, check_against="")
    assert [(x.status, x.value, x.source) for x in a] == [
        ("filled", None, "legal_first_name"),
        ("filled", None, "email"),
        ("needs_you", None, ""),
        ("filled", ["Yes"], "needs_sponsorship"),
        ("filled", ["May - July 2027"], "graduation"),
        ("filled", "tailored résumé PDF", "resume"),
        ("skipped", None, ""),
    ]
    assert a[2].note == "not in your profile: phone"
    assert "ada@example.com" not in json.dumps([x.to_dict() for x in a])


def test_resolve_never_consents_and_declines_demographics() -> None:
    questions = [
        q("I confirm the information is true", "boolean"),
        q("We record interviews with an AI notetaker. OK?", "select", options=["Yes", "No"]),
        q(
            "Gender",
            "select",
            required=False,
            options=["Male", "Female", "Decline to Self Identify"],
            demographic=True,
        ),
        q("Race", "select", required=False, options=["A", "B"], demographic=True),
    ]
    # even when the model says otherwise
    replies = Replies(
        replies=[
            Reply(id="q1", kind="profile", profile_key="email"),
            Reply(id="q2", kind="consent"),
            Reply(id="q3", kind="open", answer="Male"),
            Reply(id="q4", kind="skip"),
        ]
    )
    a = resolve(questions, replies, ME, check_against="")
    assert [x.status for x in a] == ["needs_you", "needs_you", "filled", "skipped"]
    assert a[2].value == ["Decline to Self Identify"]


def test_resolve_flags_invented_facts_in_drafts_and_waits_on_the_unknown() -> None:
    questions = [
        q("Why us?", "long_text"),
        q("Anything else?", "long_text", required=False),
        q("Favourite food?", required=False),
        q("Mystery", required=True),
    ]
    replies = Replies(
        replies=[
            Reply(id="q1", kind="open", answer="I built an agent with 17 tests; I'd use Kubernetes at Acme."),
            Reply(id="q2", kind="open", answer=" "),
            Reply(id="q3", kind="skip"),
        ]
    )
    a = resolve(questions, replies, None, check_against="Wrote 17 offline tests.\nAcme")
    assert a[0].status == "drafted" and a[0].note == "check: Kubernetes not in the résumé or posting"
    assert [x.status for x in a[1:]] == ["skipped", "skipped", "needs_you"]


def test_render_shows_profile_fields_by_name_only() -> None:
    a = resolve(
        [q("Email"), q("Why us?", "long_text"), q("Phone")],
        Replies(
            replies=[
                Reply(id="q1", kind="profile", profile_key="email"),
                Reply(id="q2", kind="open", answer="Because agents."),
                Reply(id="q3", kind="profile", profile_key="phone"),
            ]
        ),
        ME,
        check_against="agents",
    )
    text = answer.render([x.to_dict() for x in a], "#7 Acme")
    assert text.startswith("#7 Acme: application form, 3 questions")
    assert text.index("❗ Needs you (1)") < text.index("✎ Why us?") < text.index("✓ Filled (1)")
    assert "• Email → ‹email›" in text and "ada@" not in text


# --- the profile file ---


def test_profile_is_encrypted_and_needs_its_key(tmp_path) -> None:
    key, path = profiles.new_key(), tmp_path / "profile.enc"
    profiles.save_text('email = "ada@example.com"\nneeds_sponsorship = true\n', path, key)
    assert b"ada@example.com" not in path.read_bytes()
    assert profiles.load(path, key).email == "ada@example.com"
    assert oct(path.stat().st_mode)[-3:] == "600"
    with pytest.raises(RuntimeError, match="wrong key"):
        profiles.load(path, profiles.new_key())
    with pytest.raises(ValueError):
        profiles.save_text("needs_sponsorship = 'maybe'\n", path, key)
    assert profiles.load(tmp_path / "missing.enc", key) is None


def test_profile_edit_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(profiles.KEY_ENV, profiles.new_key())
    editor = tmp_path / "editor.sh"
    editor.write_text('#!/bin/sh\necho \'phone = "+65 0000 0000"\' >> "$1"\n')
    editor.chmod(0o755)
    path = tmp_path / "profile.enc"
    assert profiles.edit(path, str(editor)).phone == "+65 0000 0000"
    assert "# legal_first_name" in profiles.template()


# --- needs Postgres (TRAWL_TEST_DATABASE_URL) ---

TEX = r"""\documentclass{x}
\begin{document}
\section{Projects}
\entry{Solfish}{2026}
\begin{itemize}
  \item Wrote 17 offline tests.
\end{itemize}
\end{document}
"""


def test_tailor_new_drafts_the_form_with_the_resume(store, board_http) -> None:
    store.upsert(
        [
            Posting(
                "ashby",
                "abc",
                "Sierra",
                "Forward Deployed Engineer",
                "https://jobs.ashbyhq.com/Sierra/abc",
                "Singapore",
                None,
                description="Build agents with customers.",
            )
        ]
    )
    process(
        store,
        Criteria(),
        board_http,
        FakeClient(job_facts(), matches=[{"score": 90, "strengths": [], "gaps": [], "angle": "x"}]),
        ("v", "h"),
    )
    replies = {
        "replies": [
            {"id": "q1", "kind": "profile", "profile_key": "legal_first_name"},
            {"id": "q10", "kind": "open", "answer": "I wrote 17 offline tests for an agent."},
        ]
    }
    client = FakeClient(tailorings=[{"summary": "", "entries": [], "skills": [], "changes": []}])
    client.answers = [replies]  # record_answers isn't a match or tailoring, so it reads `answers`
    done, failed = tailor_new(
        store,
        client,
        parse(TEX),
        "r1",
        lambda tex: (b"%PDF", 1),
        min_score=60,
        limit=5,
        http=form_http(),
        profile=ME,
    )
    assert (done, failed) == (1, [])
    [app] = store.unsent_applications([r.id for r in store.undigested().rows])
    def by_label(start: str) -> dict:
        return next(a for a in app.answers if a["question"]["label"].startswith(start))

    assert by_label("Legal First Name")["status"] == "filled"
    assert by_label("Why are you excited")["value"] == "I wrote 17 offline tests for an agent."
    assert by_label("Trust is one of")["status"] == "needs_you"
    prompt = client.requests[-1]["messages"][0]["content"]
    assert "Ada" not in prompt and "[q10] required long_text: Why are you excited" in prompt
