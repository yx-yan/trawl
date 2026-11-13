from __future__ import annotations

import pytest
from conftest import FakeClient, job_facts

from trawl.decide import Criteria, decide, prefilter
from trawl.extract import TOOL, JobFacts, extract, extract_with_retry

C = Criteria(graduation="2027-06", needs_sponsorship=True)  # a 2027 graduate who needs a work pass


def verdict(title: str = "Forward Deployed Engineer", facts: dict | None = None, **job: object):
    return decide(title, facts or {}, JobFacts.model_validate(job_facts(**job)), C)


def test_new_grad_fde_is_eligible() -> None:
    v = verdict()
    assert v.eligible and v.reasons == []


@pytest.mark.parametrize(("title", "reason"), [
    ("Senior Forward Deployed Engineer", "senior title (Senior)"),
    ("Sr. Applied AI Engineer", "senior title (Sr)"),
    ("Engagement Manager, FDE", "senior title (Manager)"),
    ("Head of ASEAN, Applied AI", "senior title (Head)"),
    ("Accountant", "not a technical title"),
    ("International Accounting, APAC", "not a technical title"),
])
def test_prefilter_titles(title: str, reason: str) -> None:
    assert reason in prefilter(title, {}, C)


@pytest.mark.parametrize("title", [
    "Forward Deployed Engineer", "Software Engineer Intern", "Graduate Associate, Technology",
    "Partner Solutions Engineer", "AI Engineer",
])
def test_prefilter_keeps_junior_technical_titles(title: str) -> None:
    assert prefilter(title, {}, C) == []


@pytest.mark.parametrize(("facts", "reason"), [
    ({"employment_types": ["Contract"]}, "employment: contract"),
    ({"position_levels": ["Manager"]}, "level: manager"),
    ({"min_years": 5}, "needs 5+ years"),
    ({"ssic": "78104"}, "posted by an agency"),
    ({"salary_max": 3000, "employment_types": ["Full Time"]}, "pay up to S$3000 is below the S Pass minimum"),
])
def test_prefilter_board_facts(facts: dict, reason: str) -> None:
    assert prefilter("Software Engineer", facts, C) == [reason]


def test_prefilter_keeps_mixed_employment_and_low_paid_internships() -> None:
    assert prefilter("Software Engineer", {"employment_types": ["Contract", "Full Time"]}, C) == []
    intern = {"employment_types": ["Internship/Attachment"], "salary_max": 1500}
    assert prefilter("Software Engineer Intern", intern, C) == []


@pytest.mark.parametrize(("job", "reason"), [
    ({"track": "other", "reason": "sales role"}, "not FDE or a path to it: sales role"),
    ({"min_years": 3}, "needs 3+ years"),
    ({"seniority": "senior"}, "senior-level"),
    ({"employment": "contract"}, "employment: contract"),
    ({"agency": True}, "posted by an agency"),
    ({"work_authorization": "citizens_pr_only"}, "citizens / PR only"),
    ({"work_authorization": "no_sponsorship"}, "no visa sponsorship"),
    ({"work_authorization": "clearance_required"}, "security clearance"),
    ({"requires_phd": True}, "PhD required"),
    ({"based_in_singapore": False}, "based outside Singapore"),
    ({"graduation_from": "2027-12"}, "for graduates from 2027-12"),
    ({"graduation_to": "2026-12"}, "for graduates up to 2026-12"),
    ({"required_languages": ["Japanese"]}, "requires Japanese"),
    ({"writes_code": False}, "doesn't write code"),
])
def test_job_facts_reject(job: dict, reason: str) -> None:
    v = verdict(**job)
    assert not v.eligible and reason in v.reasons


def test_mandarin_and_two_years_are_fine() -> None:
    assert verdict(required_languages=["Mandarin"], min_years=2).eligible


def test_graduation_window_including_the_candidate_is_fine() -> None:
    assert verdict(graduation_from="2026-09", graduation_to="2027-08").eligible


def test_preferring_locals_is_a_flag_not_a_reason() -> None:
    v = verdict(work_authorization="prefers_local")
    assert v.eligible and v.flags == ["prefers local candidates"]


def test_null_strings_and_bad_dates_become_none() -> None:
    v = verdict(intake="null", graduation_from="null", graduation_to="late 2027")
    assert v.eligible and v.flags == []
    assert len(verdict(intake="x" * 100).flags[0]) == 60


def test_flags() -> None:
    v = verdict(facts={"salary_max": 5000}, work_authorization="open", intake="2027 graduates")
    assert v.eligible
    assert v.flags == ["S Pass range pay", "sponsorship mentioned", "2027 graduates"]
    intern = verdict(seniority="intern", employment="internship", facts={"salary_max": 1500})
    assert intern.flags == ["internship"]


def test_same_reason_from_board_and_model_counted_once() -> None:
    v = verdict(facts={"min_years": 4}, min_years=4)
    assert v.reasons.count("needs 4+ years") == 1


def test_neutral_defaults_skip_sponsorship_and_graduation_rules() -> None:
    job = JobFacts.model_validate(job_facts(work_authorization="citizens_pr_only", graduation_from="2030-01"))
    assert not decide("Software Engineer", {"salary_max": 3000}, job, C).eligible
    assert decide("Software Engineer", {"salary_max": 3000}, job, Criteria()).eligible


def test_extract_forces_the_tool_and_parses() -> None:
    client = FakeClient(job_facts(track="path", seniority="intern"))
    job, usage = extract(client, company="Acme", title="SWE Intern", location="Singapore",
                         description="x" * 50_000)
    assert job.track == "path" and usage.input_tokens == 1000
    req = client.requests[0]
    assert req["tool_choice"] == {"type": "tool", "name": TOOL["name"]}
    assert req["thinking"] == {"type": "disabled"}
    assert len(req["messages"][0]["content"]) < 13_000  # description truncated


def test_extract_rejects_malformed_answer() -> None:
    with pytest.raises(ValueError):
        extract(FakeClient({"track": "maybe"}), company="A", title="B", location="C", description="D")


def test_extract_retries_an_empty_answer_once() -> None:
    client = FakeClient({}, job_facts())
    job, _ = extract_with_retry(client, company="A", title="B", location="C", description="D")
    assert job.track == "fde" and len(client.requests) == 2
