from __future__ import annotations

import httpx
import pytest

from trawl.config import Sources
from trawl.connectors import CONNECTORS, ashby, greenhouse, lever, mcf
from trawl.models import Posting


def test_greenhouse(board_http: httpx.Client) -> None:
    jobs = greenhouse.fetch(board_http, "anthropic")
    assert [j.title for j in jobs] == ["Applied AI Architect", "3P Revenue Accounting & Reporting"]
    first = jobs[0]
    assert first.source == "greenhouse" and first.company == "Anthropic"
    assert "Mumbai" in first.location
    assert first.url.startswith("https://job-boards.greenhouse.io/anthropic/jobs/")
    assert first.posted_at is not None
    assert "<" not in first.description and "&lt;" not in first.description


def test_lever(board_http: httpx.Client) -> None:
    jobs = lever.fetch(board_http, "palantir")
    assert jobs[0].company == "Palantir" and jobs[0].location.startswith("Singapore")
    assert jobs[0].posted_at is not None and jobs[0].description


def test_lever_unknown_board_raises(board_http: httpx.Client) -> None:
    with pytest.raises(httpx.HTTPError):
        lever.fetch(board_http, "nope")


def test_ashby(board_http: httpx.Client) -> None:
    jobs = ashby.fetch(board_http, "sierra")
    assert jobs[0].title == "Enterprise Sales Engineer" and jobs[0].company == "Sierra"
    assert "US - Remote" in jobs[0].location


def test_mcf(board_http: httpx.Client) -> None:
    jobs = mcf.fetch(board_http, "forward deployed engineer")
    assert jobs[0].title.endswith("Forward Deployed Engineer")
    assert jobs[0].company == "RECRUIT EXPRESS PTE LTD" and jobs[0].location == "Singapore"
    assert jobs[0].url.startswith("https://www.mycareersfuture.gov.sg/job/")


def test_every_connector_registered() -> None:
    assert set(CONNECTORS) == {"greenhouse", "lever", "ashby", "mcf"}


def test_mcf_facts(board_http: httpx.Client) -> None:
    job = mcf.fetch(board_http, "x")[0]
    assert job.facts["employment_types"] == ["Contract"]
    assert job.facts["salary_max"] == 7500
    description, facts = mcf.describe(board_http, job.external_id)
    assert "Responsibilities" in description
    assert facts == {"min_years": 5, "ssic": "78104"}


def test_sources_wants() -> None:
    sources = Sources(locations=["Singapore"])
    assert sources.wants("Singapore, Singapore")
    assert sources.wants("Hybrid | Singapore | Sydney, Australia")
    assert not sources.wants("San Francisco, CA | Seattle, WA")


def test_fingerprint_matches_across_boards() -> None:
    def posting(company: str, title: str, source: str) -> Posting:
        return Posting(source, "1", company, title, "u", "Singapore", None)

    a = posting("ANTHROPIC SINGAPORE PTE. LTD.", "Applied AI Engineer", "mcf")
    b = posting("Anthropic", "Applied AI Engineer", "greenhouse")
    c = posting("Anthropic", "Applied AI Architect", "greenhouse")
    assert a.fingerprint == b.fingerprint != c.fingerprint
