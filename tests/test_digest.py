from __future__ import annotations

from datetime import datetime

import httpx

from trawl import digest
from trawl.config import Sources
from trawl.models import Posting
from trawl.store import Row


def row(i: int, company: str, title: str, tier: str | None) -> Row:
    return Row(i, "greenhouse", company, title, f"https://x/{i}", "Singapore", tier, None)


def test_render_groups_by_tier() -> None:
    text = digest.render([row(1, "Zeta", "Solutions Engineer", "adjacent"),
                          row(2, "Acme", "Forward Deployed Engineer", "core")])
    assert text.startswith("trawl: 2 new matching jobs")
    assert text.index("FDE-like titles (1)") < text.index("Acme") < text.index("Adjacent titles (1)")


def test_render_empty() -> None:
    assert "no new" in digest.render([])


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

def posting(source: str, ext: str, company: str, title: str, location: str = "Singapore") -> Posting:
    return Posting(source, ext, company, title, f"https://{source}/{ext}", location, datetime(2026, 11, 1))


def test_upsert_counts_only_new(store) -> None:
    jobs = [posting("greenhouse", "1", "Anthropic", "Applied AI Engineer")]
    assert store.upsert(jobs) == 1
    assert store.upsert(jobs) == 0


def test_digest_dedupes_across_boards_and_runs(store) -> None:
    store.upsert([
        posting("mcf", "a", "ANTHROPIC SINGAPORE PTE. LTD.", "Applied AI Engineer"),
        posting("greenhouse", "1", "Anthropic", "Applied AI Engineer"),
        posting("greenhouse", "2", "Anthropic", "Recruiter"),
    ])
    rows = store.undigested()
    assert [(r.source, r.title) for r in rows] == [("greenhouse", "Applied AI Engineer")]
    store.mark_digested(rows)
    assert store.undigested() == []
    # committed, not just visible to this connection (a SELECT before the UPDATE once left it uncommitted)
    fresh = store.reconnect()
    assert fresh.undigested() == []
    fresh.conn.close()
    # the same job reappearing on yet another board is not sent again
    store.upsert([posting("ashby", "z", "Anthropic", "Applied AI Engineer")])
    assert store.undigested() == []


def test_collect_skips_failing_board(store, board_http: httpx.Client) -> None:
    from trawl.cli import collect

    sources = Sources(locations=["Singapore"], boards={"lever": ["nope", "palantir"]})
    assert collect(store, sources, board_http) == 1  # the Singapore Palantir posting
