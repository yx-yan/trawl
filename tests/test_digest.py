from __future__ import annotations

from collections import Counter
from datetime import datetime

import httpx
from conftest import FakeClient, job_facts

from trawl import digest
from trawl.config import Sources
from trawl.decide import Criteria, Verdict
from trawl.models import Posting
from trawl.process import process, redecide
from trawl.store import Batch, Row


def row(i: int, company: str, title: str, track: str, flags: list[str] | None = None) -> Row:
    return Row(i, "greenhouse", company, title, f"https://x/{i}", "Singapore", track, None, flags or [])


def test_render_groups_by_track_with_flags_and_tally() -> None:
    batch = Batch(rows=[row(1, "Zeta", "Software Engineer", "path", ["internship"]),
                        row(2, "Acme", "Forward Deployed Engineer", "fde")],
                  rejected=Counter({"senior title": 7, "posted by an agency": 2}), decided_ids=[])
    text = digest.render(batch)
    assert text.startswith("trawl: 2 new jobs you can apply to")
    assert text.index("FDE roles (1)") < text.index("Acme") < text.index("Paths into FDE (1)")
    assert "Software Engineer [internship]" in text
    assert text.endswith("Filtered out 9: 7 senior title, 2 posted by an agency")


def test_render_empty() -> None:
    assert "no new jobs" in digest.render(Batch([], Counter(), []))


def test_split_keeps_lines_whole() -> None:
    text = "\n".join(f"line {i:03d} " + "x" * 40 for i in range(200))
    parts = digest.split(text, limit=500)
    assert all(len(p) <= 500 for p in parts)
    assert "\n".join(parts) == text


def test_send_telegram() -> None:
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    digest.send_telegram("hello", "TOKEN", "42", http)
    assert sent[0].url.path == "/botTOKEN/sendMessage"


# --- needs Postgres (TRAWL_TEST_DATABASE_URL) ---

def posting(source: str, ext: str, company: str, title: str, **facts: object) -> Posting:
    return Posting(source, ext, company, title, f"https://{source}/{ext}", "Singapore",
                   datetime(2026, 11, 1), description="We build agents with customers.", facts=facts)


def test_upsert_counts_only_new(store) -> None:
    jobs = [posting("greenhouse", "1", "Anthropic", "Applied AI Engineer")]
    assert store.upsert(jobs) == 1
    assert store.upsert(jobs) == 0


def test_process_rules_then_model(store, board_http: httpx.Client) -> None:
    store.upsert([
        posting("greenhouse", "1", "Anthropic", "Applied AI Engineer"),
        posting("greenhouse", "2", "Anthropic", "Senior Applied AI Engineer"),
        posting("greenhouse", "3", "Acme", "Solutions Engineer"),
    ])
    client = FakeClient(job_facts())
    stats = process(store, Criteria(), board_http, client)
    assert (stats.rules, stats.read, stats.eligible) == (1, 2, 2)
    assert len(client.requests) == 2  # the senior title never reached the model


def test_process_fetches_mcf_detail_and_rejects_agency_without_model(store, board_http) -> None:
    store.upsert([Posting("mcf", "df2f", "RECRUIT EXPRESS PTE LTD", "Forward Deployed Engineer",
                          "https://mcf/df2f", "Singapore", None, facts={"employment_types": ["Full Time"]})])
    client = FakeClient(job_facts())
    stats = process(store, Criteria(), board_http, client)
    assert stats.rules == 1 and client.requests == []  # detail said: agency SSIC, 5 years
    reasons = store.conn.execute("SELECT reasons FROM postings").fetchone()["reasons"]
    assert reasons == ["needs 5+ years", "posted by an agency"]


def test_digest_sends_eligible_once_across_boards_and_tallies_rest(store) -> None:
    store.upsert([
        posting("mcf", "a", "ANTHROPIC SINGAPORE PTE. LTD.", "Applied AI Engineer"),
        posting("greenhouse", "1", "Anthropic", "Applied AI Engineer"),
        posting("greenhouse", "2", "Anthropic", "Staff Engineer"),
    ])
    ids = {r["external_id"]: r["id"] for r in store.conn.execute("SELECT id, external_id FROM postings")}
    store.save_verdict(ids["a"], Verdict(True), job_facts=job_facts())
    store.save_verdict(ids["1"], Verdict(True), job_facts=job_facts())
    store.save_verdict(ids["2"], Verdict(False, ["senior title (Staff)"]))
    batch = store.undigested()
    sent = [(r.source, r.title, r.track) for r in batch.rows]
    assert sent == [("greenhouse", "Applied AI Engineer", "fde")]
    assert batch.rejected == Counter({"senior title": 1})
    store.mark_digested(batch)
    fresh = store.reconnect()  # committed, not just visible to this connection
    assert fresh.undigested().rows == [] and not fresh.undigested().rejected
    fresh.conn.close()
    # the same job reappearing on yet another board is not sent again
    store.upsert([posting("ashby", "z", "Anthropic", "Applied AI Engineer")])
    store.save_verdict(store.conn.execute("SELECT max(id) AS id FROM postings").fetchone()["id"],
                       Verdict(True), job_facts=job_facts())
    assert store.undigested().rows == []


def test_redecide_applies_new_criteria_without_the_model(store, board_http) -> None:
    store.upsert([posting("greenhouse", "1", "Acme", "Software Engineer")])
    process(store, Criteria(), board_http, FakeClient(job_facts(min_years=3)))
    assert store.undigested().rows == []
    redecide(store, Criteria(max_years=3))
    assert [r.title for r in store.undigested().rows] == ["Software Engineer"]


def test_collect_skips_failing_board(store, board_http: httpx.Client) -> None:
    from trawl.cli import collect

    sources = Sources(locations=["Singapore"], boards={"lever": ["nope", "palantir"]})
    assert collect(store, sources, board_http) == 1  # the Singapore Palantir posting
