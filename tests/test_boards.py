"""Workday and SmartRecruiters: their lists (paged, Singapore only), what the first rules can read
from them, and the descriptions fetched later; all offline."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
import pytest
from conftest import FakeClient, job_facts

from trawl.connectors import DESCRIBE, smartrecruiters, workday
from trawl.decide import Criteria
from trawl.models import Posting
from trawl.process import process

WD_API = "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/External"
WD_JOBS = [
    {
        "title": "Forward Deployed Engineer ",
        "externalPath": "/job/Singapore/FDE_JR1",
        "locationsText": "Singapore",
        "postedOn": "Posted Today",
    },
    {
        "title": "AI Engineer",
        "externalPath": "/job/Singapore-One-North/AI-Engineer_JR2",
        "locationsText": "2 Locations",
        "postedOn": "Posted 3 Days Ago",
    },
    {
        "title": "Graduate Software Engineer",
        "externalPath": "/job/Singapore/Grad_JR3",
        "locationsText": "Singapore",
        "postedOn": "Posted 30+ Days Ago",
    },
]


def workday_http(seen: list) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/json"  # a server gets 429 without it
        if request.method == "POST" and str(request.url) == f"{WD_API}/jobs":
            body = json.loads(request.content)
            seen.append(body)
            page = WD_JOBS[body["offset"] : body["offset"] + body["limit"]]
            # Workday gives the total only with the first page
            return httpx.Response(
                200, json={"total": len(WD_JOBS) if body["offset"] == 0 else 0, "jobPostings": page}
            )
        if str(request.url) == f"{WD_API}/job/Singapore/FDE_JR1":
            return httpx.Response(
                200,
                json={
                    "jobPostingInfo": {
                        "jobDescription": "<p>Build with customers.</p>",
                        "timeType": "Full time",
                    }
                },
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_workday_pages_through_a_singapore_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workday, "PAGE", 2)
    seen: list = []
    postings = workday.fetch(workday_http(seen), "acme.wd3/External")
    assert [b["offset"] for b in seen] == [0, 2] and {b["searchText"] for b in seen} == {"Singapore"}
    assert [p.title for p in postings] == [
        "Forward Deployed Engineer",
        "AI Engineer",
        "Graduate Software Engineer",
    ]
    first = postings[0]
    assert (first.source, first.company, first.external_id) == ("workday", "Acme", "/job/Singapore/FDE_JR1")
    assert first.url == "https://acme.wd3.myworkdayjobs.com/External/job/Singapore/FDE_JR1"
    assert first.facts == {"board": "acme.wd3/External"}
    assert postings[1].location == "Singapore One North (+ more)"  # "2 Locations": the path names the first


def test_workday_posted_dates() -> None:
    now = datetime(2026, 11, 23).astimezone()
    assert workday.posted("Posted Today", now) == now
    assert workday.posted("Posted Yesterday", now) == now - timedelta(days=1)
    assert workday.posted("Posted 30+ Days Ago", now) == now - timedelta(days=30)
    assert workday.posted("Posted sometime", now) is None


def test_workday_describes_one_posting() -> None:
    text, extra = DESCRIBE["workday"](
        workday_http([]), "/job/Singapore/FDE_JR1", {"board": "acme.wd3/External"}
    )
    assert text == "Build with customers." and extra == {"employment_types": ["Full time"]}


SR_JOBS = [
    {
        "id": "1",
        "name": "Software Engineer Intern",
        "company": {"name": "Acme"},
        "location": {"fullLocation": "Singapore, Singapore"},
        "releasedDate": "2026-11-20T10:00:00.000Z",
        "typeOfEmployment": {"label": "Internship"},
        "experienceLevel": {"id": "entry_level"},
    },
    {
        "id": "2",
        "name": "Director, Engineering",
        "company": {"name": "Acme"},
        "location": {"city": "Singapore", "country": "sg"},
        "typeOfEmployment": {"label": "Full-time"},
        "experienceLevel": {"id": "director"},
    },
]


def smartrecruiters_http(seen: list) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/companies/Acme/postings":
            seen.append(dict(request.url.params))
            offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
            return httpx.Response(
                200, json={"totalFound": len(SR_JOBS), "content": SR_JOBS[offset : offset + limit]}
            )
        if request.url.path == "/v1/companies/Acme/postings/1":
            return httpx.Response(
                200,
                json={
                    "jobAd": {
                        "sections": {
                            "jobDescription": {"text": "<p>Ship agents.</p>"},
                            "qualifications": {"text": "<ul><li>Python</li></ul>"},
                            "companyDescription": {"text": "<p>Acme makes things.</p>"},
                        }
                    }
                },
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_smartrecruiters_reads_singapore_postings_with_what_the_rules_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smartrecruiters, "PAGE", 1)
    seen: list = []
    intern, director = smartrecruiters.fetch(smartrecruiters_http(seen), "Acme")
    assert [s["offset"] for s in seen] == ["0", "1"] and {s["country"] for s in seen} == {"sg"}
    assert (
        intern.url == "https://jobs.smartrecruiters.com/Acme/1" and intern.location == "Singapore, Singapore"
    )
    assert intern.facts == {"board": "Acme", "employment_types": ["internship/attachment"]}
    assert director.location == "Singapore | sg"
    assert director.facts["position_levels"] == ["senior management"]  # the rules' word for it


def test_smartrecruiters_describes_one_posting() -> None:
    text, _ = DESCRIBE["smartrecruiters"](smartrecruiters_http([]), "1", {"board": "Acme"})
    assert text == "Ship agents.\n\nPython\n\nAcme makes things."


def test_process_fetches_a_workday_description_before_the_model_reads(store) -> None:  # noqa: ANN001
    store.upsert(
        [
            Posting(
                "workday",
                "/job/Singapore/FDE_JR1",
                "Acme",
                "Forward Deployed Engineer",
                "https://acme.wd3.myworkdayjobs.com/External/job/Singapore/FDE_JR1",
                "Singapore",
                None,
                facts={"board": "acme.wd3/External"},
            )
        ]
    )
    client = FakeClient(job_facts())
    stats = process(store, Criteria(), workday_http([]), client)
    assert stats.read == 1 and "Build with customers." in str(client.requests[0])
    row = store.conn.execute("SELECT description, facts FROM postings").fetchone()
    assert row["description"] == "Build with customers."
    assert row["facts"]["employment_types"] == ["Full time"]
