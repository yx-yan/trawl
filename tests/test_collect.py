from __future__ import annotations

import httpx
import pytest

from trawl.connectors import CONNECTORS, ashby, greenhouse, lever, mcf
from trawl.models import Posting
from trawl.titles import in_locations, title_tier


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


@pytest.mark.parametrize(("title", "tier"), [
    ("Forward Deployed Engineer", "core"),
    ("Forward-Deployed Software Engineer, APAC", "core"),
    ("AI Engineer | AI & Digital Solutions Forward Deployed Engineer", "core"),
    ("Applied AI Architect, Commercial", "core"),
    ("Agent Engineer", "core"),
    ("Deployment Strategist", "core"),
    ("Solutions Architect, GenAI", "core"),
    ("Enterprise Sales Engineer", "adjacent"),
    ("Customer Engineer, Google Cloud", "adjacent"),
    ("Solutions Engineer", "adjacent"),
    ("GenAI Engineer", "adjacent"),
    ("3P Revenue Accounting & Reporting", None),
    ("Administrative Business Partner", None),
    ("Sr Product Manager, Technical, AWS Forward Deployed Engineering (FDE)", None),
    ("Head of ASEAN, Applied AI", None),
])
def test_title_tier(title: str, tier: str | None) -> None:
    assert title_tier(title) == tier


def test_in_locations() -> None:
    wanted = ["Singapore", "APAC"]
    assert in_locations("Singapore, Singapore", wanted)
    assert in_locations("Remote | APAC", wanted)
    assert not in_locations("San Francisco, CA | Seattle, WA", wanted)


def test_fingerprint_matches_across_boards() -> None:
    def posting(company: str, title: str, source: str) -> Posting:
        return Posting(source, "1", company, title, "u", "Singapore", None)

    a = posting("ANTHROPIC SINGAPORE PTE. LTD.", "Applied AI Engineer", "mcf")
    b = posting("Anthropic", "Applied AI Engineer", "greenhouse")
    c = posting("Anthropic", "Applied AI Architect", "greenhouse")
    assert a.fingerprint == b.fingerprint != c.fingerprint
