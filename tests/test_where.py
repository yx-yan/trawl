"""Where else to apply for a MyCareersFuture job: boards guessed from the company, its website and
careers page, and searches; all offline (boards and sites are faked)."""

from __future__ import annotations

import httpx
import pytest

from trawl import where
from trawl.models import Posting

TITLE = "AI Product Engineer Intern"


def posting(title: str, location: str = "Singapore", url: str = "https://jobs.lever.co/dcscc/1") -> Posting:
    return Posting("lever", "1", "Dcscc", title, url, location, None)


def test_slugs_from_the_name_and_the_website() -> None:
    whole, first = where.slugs("DCS CARD CENTRE PTE. LTD.", "https://www.dcscc.com/")
    assert whole == ["dcs-card-centre", "dcscardcentre", "dcscc"]
    assert first == ["dcs"]  # a single word: trusted only with the job on it
    assert where.slugs("Stripe") == (["stripe"], [])


@pytest.fixture
def boards(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Fake boards by (board, slug): a list of postings, or missing (the board answers 404)."""
    known: dict = {}

    def connector(board: str):  # noqa: ANN202
        def fetch(_http, slug):  # noqa: ANN001, ANN202
            if (board, slug) not in known:
                raise httpx.HTTPStatusError("404", request=httpx.Request("GET", "x"),
                                            response=httpx.Response(404))
            return known[(board, slug)]
        return fetch

    monkeypatch.setattr(where, "CONNECTORS", {b: connector(b) for b in where.BOARDS})
    return known


def test_a_board_with_the_job_is_a_link_trawl_can_fill(boards: dict) -> None:
    boards[("lever", "dcscc")] = [posting("Data Analyst"), posting("AI Product Engineer Intern (2027)")]
    [lead] = where.board_leads(httpx.Client(), "DCS CARD CENTRE PTE. LTD.", TITLE, "https://www.dcscc.com/")
    assert (lead.kind, lead.fillable, lead.board, lead.slug) == ("posting", True, "lever", "dcscc")


def test_a_board_without_the_job(boards: dict) -> None:
    boards[("greenhouse", "dcscc")] = [posting("Data Analyst")]  # the company's own: worth a look
    boards[("ashby", "dcs")] = [posting("Data Analyst")]  # a guess from one word: maybe someone else's
    leads = where.board_leads(httpx.Client(), "DCS CARD CENTRE PTE. LTD.", TITLE, "https://www.dcscc.com/")
    assert [(lead.kind, lead.url) for lead in leads] == [("board", "https://job-boards.greenhouse.io/dcscc")]


def test_a_one_word_guess_counts_with_the_job_on_it(boards: dict) -> None:
    boards[("ashby", "dcs")] = [posting(TITLE, url="https://jobs.ashbyhq.com/dcs/9")]
    [lead] = where.board_leads(httpx.Client(), "DCS CARD CENTRE PTE. LTD.", TITLE, None)
    assert lead.url == "https://jobs.ashbyhq.com/dcs/9"


def test_a_job_elsewhere_than_singapore_isnt_this_one(boards: dict) -> None:
    boards[("lever", "dcscc")] = [posting(TITLE, location="London")]
    assert [lead.kind for lead in where.board_leads(httpx.Client(), "DCS CARD CENTRE PTE. LTD.", TITLE,
                                                    "https://www.dcscc.com/")] == ["board"]


def site_http(careers: bool) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.mycareersfuture.gov.sg":
            if request.url.path == "/v2/jobs/abc":
                return httpx.Response(200, json={"postedCompany": {"uen": "201826570H"}})
            if request.url.path == "/v2/companies/201826570H":
                return httpx.Response(200, json={"companyUrl": "www.dcscc.com"})
        if request.url.host == "www.dcscc.com" and request.url.path == "/careers" and careers:
            return httpx.Response(200, text="<html>jobs</html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_leads_put_the_most_direct_first(boards: dict) -> None:
    found = where.leads(site_http(careers=True), source="mcf", external_id="abc",
                        company="DCS CARD CENTRE PTE. LTD.", title=TITLE,
                        siblings=[{"id": 7, "source": "greenhouse",
                                   "url": "https://job-boards.greenhouse.io/x/1"},
                                  {"id": 8, "source": "mcf", "url": "https://mcf/other-copy"}])
    assert [(lead.kind, lead.url) for lead in found] == [
        ("posting", "https://job-boards.greenhouse.io/x/1"),
        ("careers", "https://www.dcscc.com/careers"),
        ("website", "https://www.dcscc.com"),
        ("search", found[3].url), ("search", found[4].url)]
    assert found[0].fillable
    assert "linkedin.com/jobs/search" in found[3].url and "location=Singapore" in found[3].url


def test_no_website_still_gives_searches(boards: dict) -> None:
    found = where.leads(site_http(careers=False), source="mcf", external_id="missing",
                        company="Nobody Pte Ltd", title=TITLE, siblings=[])
    assert [lead.kind for lead in found] == ["search", "search"]


def test_find_keeps_the_leads_and_the_app_shows_them(store, boards: dict,  # noqa: ANN001
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from trawl.desktop.server import create_app

    store.upsert([Posting("mcf", "abc", "DCS CARD CENTRE PTE. LTD.", TITLE, "https://mcf/abc", "Singapore",
                          None)])
    pid = store.conn.execute("SELECT id FROM postings").fetchone()["id"]
    store.conn.execute("UPDATE postings SET eligible = true")
    assert store.without_leads() == [pid]
    monkeypatch.setattr(where, "client", lambda: site_http(careers=True))
    client = TestClient(create_app(store))
    leads = client.post(f"/api/jobs/{pid}/leads").json()["leads"]
    assert [lead["kind"] for lead in leads] == ["careers", "website", "search", "search"]
    job = client.get(f"/api/jobs/{pid}").json()
    assert job["leads"] == leads and job["leads_at"]
    assert client.get("/api/jobs").json()[0]["has_leads"] is True
    assert store.without_leads() == []
