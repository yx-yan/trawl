from __future__ import annotations

import pytest
from conftest import FakeClient, job_facts

from trawl import compare as c
from trawl.decide import Criteria
from trawl.models import Posting
from trawl.process import process

RESUME = "- Wrote agents in Python, with pytest.\n- C++ coursework"
POSTING = "You write Python. RAG is a plus. Docker required."


def req(skill: str, quote: str, status: str, evidence: str = "", must: bool = True) -> dict:
    return {"skill": skill, "kind": "engineering", "must": must, "quote": quote, "status": status,
            "evidence": evidence}


def run(*reqs: dict) -> list[dict]:
    rows, _ = c.compare(FakeClient(comparisons=[{"requirements": list(reqs)}]), RESUME,
                        company="A", title="T", description=POSTING)
    return rows


def test_a_supported_claim_stays() -> None:
    [row] = run(req("Python", "you write  python", "have", "agents in Python"))
    assert (row["status"], row["evidence"]) == ("have", "agents in Python")


def test_a_claim_the_resume_doesnt_support_goes_down_a_step() -> None:
    rows = run(req("Docker", "Docker required", "have", "deployed with Docker"),
               req("RAG", "RAG is a plus", "partial", "", must=False))
    assert [(r["status"], r["evidence"]) for r in rows] == [("partial", ""), ("missing", "")]


def test_invented_and_repeated_requirements_are_dropped() -> None:
    rows = run(req("Rust", "expert in Rust", "missing"), req("RAG", "RAG is a plus", "missing"),
               req("rag", "RAG is a plus", "missing"))
    assert [r["skill"] for r in rows] == ["RAG"]


@pytest.mark.parametrize(("quote", "text", "found"), [
    ("C", "C++ coursework", False), ("C++", "C++ coursework", True),
    ("Java", "JavaScript", False), ("", "x", False)])
def test_quotes_match_whole_words(quote: str, text: str, found: bool) -> None:
    assert c.quoted(quote, text) is found


def test_a_long_list_is_cut() -> None:
    assert len(run(*[req(f"S{i}", "Docker required", "missing") for i in range(20)])) == c.LIMIT


# --- needs Postgres (TRAWL_TEST_DATABASE_URL) ---

def test_compares_each_open_job_once_per_resume(store, board_http) -> None:  # noqa: ANN001
    store.upsert([Posting("ashby", "1", "Acme", "Forward Deployed Engineer", "https://a/1", "Singapore",
                          None, description=POSTING)])
    client = FakeClient(job_facts(), comparisons=[{"requirements": [req("RAG", "RAG is a plus", "missing")]}])
    assert process(store, Criteria(), board_http, client, (RESUME, "h1")).compared == 1
    row = store.conn.execute("SELECT requirements FROM postings").fetchone()
    assert row["requirements"][0]["skill"] == "RAG"
    assert process(store, Criteria(), board_http, client, (RESUME, "h1")).compared == 0
    assert process(store, Criteria(), board_http, client, (RESUME, "h2")).compared == 1
    store.conn.execute("UPDATE postings SET last_seen = now() - interval '9 days'")
    assert process(store, Criteria(), board_http, client, (RESUME, "h3")).compared == 0  # closed


def test_the_app_compares_on_demand(store, monkeypatch, tmp_path) -> None:  # noqa: ANN001
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from trawl import extract
    from trawl.desktop.server import create_app

    store.upsert([Posting("ashby", "1", "Acme", "FDE", "https://a/1", "Singapore", None, description=POSTING),
                  Posting("ashby", "2", "Acme", "Bare", "https://a/2", "Singapore", None)])
    ids = {r["external_id"]: r["id"] for r in store.conn.execute("SELECT id, external_id FROM postings")}
    resume = tmp_path / "resume.tex"
    resume.write_text(r"\begin{document}\section{Skills} Python \end{document}")
    monkeypatch.setenv("TRAWL_RESUME", str(resume))
    monkeypatch.setattr(extract, "new_client", lambda: FakeClient(
        comparisons=[{"requirements": [req("Python", "You write Python", "have", "Python")]}]))
    client = TestClient(create_app(store))
    assert client.get(f"/api/jobs/{ids['1']}").json()["requirements"] is None
    assert client.post(f"/api/jobs/{ids['1']}/requirements").is_success
    assert client.get(f"/api/jobs/{ids['1']}").json()["requirements"][0]["status"] == "have"
    assert client.post(f"/api/jobs/{ids['2']}/requirements").status_code == 400
    assert client.post("/api/jobs/999999/requirements").status_code == 404
