from __future__ import annotations

from collections import Counter

from conftest import FakeClient, job_facts

from trawl import digest
from trawl.decide import Criteria
from trawl.match import match, resume_text
from trawl.models import Posting
from trawl.process import process
from trawl.store import Batch, Row

TEX = r"""
\documentclass{resume-en}
\begin{document}
\name{Yuxuan \surname{Yan}}
\contact{Mobile Phone: (+65) 8425 6171 / Email: \email{me@example.com}}
\section{Professional Summary}
NTU M.Sc.\ student, focused on \textbf{LLM agents}. % TODO(确认): private note
\section{Projects}
\entry{Solfish: Personal AI Agent}{Oct 2026 -- Present}
\role{Personal Project \textbar\ Python, \href{https://x.com}{DeepSeek API}}
\begin{itemize}
  \item Wrote the agent loop by hand; 17 offline tests, 50\% faster.
\end{itemize}
\end{document}
"""


def test_resume_text_keeps_content_and_drops_contact_details_and_comments() -> None:
    text = resume_text(TEX)
    assert "## Professional Summary" in text and "LLM agents" in text
    assert "Solfish: Personal AI Agent (Oct 2026 -- Present)" in text
    assert "Python, DeepSeek API" in text and "- Wrote the agent loop by hand" in text
    assert "8425" not in text and "example.com" not in text and "Yan" not in text
    assert "TODO" not in text and "\\" not in text


def test_match_parses_and_sends_the_resume() -> None:
    answer = {"score": 82, "strengths": ["agents"], "gaps": ["typescript"], "angle": "a"}
    client = FakeClient(matches=[answer])
    m, _ = match(client, "RESUME TEXT", company="Acme", title="FDE", description="JD")
    assert m.score == 82 and m.gaps == ["typescript"]
    assert "RESUME TEXT" in client.requests[0]["messages"][0]["content"]


def test_match_cuts_long_lists_and_splits_combined_gaps() -> None:
    answer = {"score": 50, "strengths": list("abcde"), "angle": "x",
              "gaps": ["C++/Go", "Java, Rust", "TypeScript", "go", "SQL", "AWS", "GCP"]}
    m, _ = match(FakeClient(matches=[answer]), "R", company="A", title="T", description="D")
    assert m.strengths == ["a", "b", "c"]
    assert m.gaps == ["c++", "go", "java", "rust", "typescript"]


def row(score: int, title: str, track: str = "fde") -> Row:
    return Row(1, "ashby", "Acme", title, "https://x", "Singapore", track, None, [],
               {"score": score, "strengths": ["agents", "evals"], "gaps": ["go", "k8s", "aws", "gcp"]})


def test_digest_sorts_by_score_and_shows_fit() -> None:
    text = digest.render(Batch([row(60, "Low"), row(90, "High")], Counter(), [],
                               resurfaced=[row(85, "Back")]))
    assert text.index("90 Acme: High") < text.index("60 Acme: Low")
    assert "+ agents, evals · − go, k8s, aws" in text and "gcp" not in text
    assert "Better fits since your résumé changed (1)" in text and "85 Acme: Back" in text


def test_render_gaps() -> None:
    text = digest.render_gaps([("typescript", 5, 2), ("rag", 1, 0)])
    assert "• typescript: 5 jobs (2 FDE)" in text
    assert text.endswith("• rag: 1 job")


# --- needs Postgres (TRAWL_TEST_DATABASE_URL) ---

def test_scores_eligible_jobs_once_per_resume_and_resurfaces_big_rises(store, board_http) -> None:
    store.upsert([Posting("ashby", "1", "Acme", "Forward Deployed Engineer", "https://a/1", "Singapore",
                          None, description="Build with customers.")])
    m = lambda s: {"score": s, "strengths": [], "gaps": ["typescript"], "angle": "x"}  # noqa: E731
    stats = process(store, Criteria(), board_http, FakeClient(job_facts(), matches=[m(55)]), ("v1", "h1"))
    assert stats.matched == 1
    store.mark_digested(store.undigested())

    again = FakeClient(job_facts(), matches=[m(99)])
    assert process(store, Criteria(), board_http, again, ("v1", "h1")).matched == 0  # same résumé
    assert again.requests == []

    process(store, Criteria(), board_http, FakeClient(job_facts(), matches=[m(70)]), ("v2", "h2"))
    batch = store.undigested()
    assert batch.rows == [] and [r.score for r in batch.resurfaced] == [70]
    store.mark_digested(batch)
    assert store.undigested().resurfaced == []
    assert store.gap_counts() == [("typescript", 1, 1)]
