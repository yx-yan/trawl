"""Postgres storage for postings and the verdicts on them.

Every posting in a wanted location is kept, whatever its title: roles are judged by their
description, so the FDE-like ones with unhelpful titles must already be here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .decide import Verdict
from .models import Posting

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    external_id TEXT NOT NULL,
    company     TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    location    TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    posted_at   TIMESTAMPTZ,
    fingerprint TEXT NOT NULL,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    digested_at TIMESTAMPTZ,
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS postings_fingerprint ON postings (fingerprint);
-- M1: board facts, the model's job facts, and the verdict
ALTER TABLE postings
    ADD COLUMN IF NOT EXISTS facts      JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS job_facts  JSONB,
    ADD COLUMN IF NOT EXISTS llm_usage  JSONB,
    ADD COLUMN IF NOT EXISTS track      TEXT,
    ADD COLUMN IF NOT EXISTS eligible   BOOLEAN,
    ADD COLUMN IF NOT EXISTS reasons    JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS flags      JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ,
    DROP COLUMN IF EXISTS title_tier;
-- M1: how well the résumé (by hash) fits each eligible job
ALTER TABLE postings
    ADD COLUMN IF NOT EXISTS match       JSONB,
    ADD COLUMN IF NOT EXISTS resume_hash TEXT,
    ADD COLUMN IF NOT EXISTS resurface   BOOLEAN NOT NULL DEFAULT false;
"""


@dataclass
class Pending:
    """A posting without a verdict yet."""

    id: int
    source: str
    external_id: str
    company: str
    title: str
    location: str
    description: str
    facts: dict
    job_facts: dict | None


@dataclass
class ToMatch:
    id: int
    company: str
    title: str
    description: str
    match: dict | None
    digested: bool


@dataclass
class Row:
    id: int
    source: str
    company: str
    title: str
    url: str
    location: str
    track: str | None
    posted_at: datetime | None
    flags: list[str] = field(default_factory=list)
    match: dict | None = None

    @property
    def score(self) -> int:
        return (self.match or {}).get("score", -1)


@dataclass
class Batch:
    """What one digest covers: the jobs to send, jobs sent before that now fit the résumé
    much better, and a tally of why the rest were dropped."""

    rows: list[Row]
    rejected: Counter[str]
    decided_ids: list[int]
    resurfaced: list[Row] = field(default_factory=list)


class Store:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    @classmethod
    def connect(cls, url: str, **kwargs: Any) -> Store:
        # autocommit: each `with conn.transaction()` is then a real transaction. Without it, a
        # SELECT leaves a transaction open, the block becomes a savepoint, and nothing commits.
        conn = psycopg.connect(url, row_factory=dict_row, autocommit=True, **kwargs)
        conn.execute(SCHEMA)
        return cls(conn)

    def upsert(self, postings: Iterable[Posting]) -> int:
        """Insert new postings, refresh seen ones; returns how many were new."""
        new = 0
        with self.conn.transaction():
            for p in postings:
                row = self.conn.execute(
                    """
                    INSERT INTO postings (source, external_id, company, title, url, location,
                                          description, posted_at, fingerprint, facts)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source, external_id) DO UPDATE SET
                        title = EXCLUDED.title, url = EXCLUDED.url, location = EXCLUDED.location,
                        description = CASE WHEN EXCLUDED.description <> ''
                                           THEN EXCLUDED.description ELSE postings.description END,
                        fingerprint = EXCLUDED.fingerprint,
                        facts = postings.facts || EXCLUDED.facts,
                        last_seen = now()
                    RETURNING (xmax = 0) AS inserted
                    """,
                    (p.source, p.external_id, p.company, p.title, p.url, p.location,
                     p.description, p.posted_at, p.fingerprint, Jsonb(p.facts)),
                ).fetchone()
                new += bool(row and row["inserted"])
        return new

    def pending(self) -> list[Pending]:
        rows = self.conn.execute(
            """
            SELECT id, source, external_id, company, title, location, description, facts, job_facts
            FROM postings WHERE decided_at IS NULL ORDER BY first_seen DESC
            """
        ).fetchall()
        return [Pending(**r) for r in rows]

    def decided_with_facts(self) -> list[Pending]:
        """Postings the model has read, to re-apply changed criteria without new model calls."""
        rows = self.conn.execute(
            """
            SELECT id, source, external_id, company, title, location, description, facts, job_facts
            FROM postings WHERE job_facts IS NOT NULL
            """
        ).fetchall()
        return [Pending(**r) for r in rows]

    def save_details(self, posting_id: int, description: str, facts: dict) -> None:
        self.conn.execute(
            "UPDATE postings SET description = %s, facts = facts || %s WHERE id = %s",
            (description, Jsonb(facts), posting_id),
        )

    def save_verdict(self, posting_id: int, verdict: Verdict, *, job_facts: dict | None = None,
                     usage: dict | None = None) -> None:
        self.conn.execute(
            """
            UPDATE postings SET eligible = %s, reasons = %s, flags = %s, decided_at = now(),
                job_facts = COALESCE(%s, job_facts), llm_usage = COALESCE(%s, llm_usage),
                track = COALESCE(%s, track)
            WHERE id = %s
            """,
            (verdict.eligible, Jsonb(verdict.reasons), Jsonb(verdict.flags),
             Jsonb(job_facts) if job_facts else None, Jsonb(usage) if usage else None,
             (job_facts or {}).get("track"), posting_id),
        )

    def to_match(self, resume_hash: str) -> list[ToMatch]:
        """Eligible postings not yet scored against this version of the résumé."""
        rows = self.conn.execute(
            """
            SELECT id, company, title, description, match, digested_at IS NOT NULL AS digested
            FROM postings WHERE eligible AND resume_hash IS DISTINCT FROM %s
            """,
            (resume_hash,),
        ).fetchall()
        return [ToMatch(**r) for r in rows]

    def save_match(self, posting_id: int, match: dict, resume_hash: str, *, resurface: bool) -> None:
        self.conn.execute(
            "UPDATE postings SET match = %s, resume_hash = %s, resurface = resurface OR %s WHERE id = %s",
            (Jsonb(match), resume_hash, resurface, posting_id),
        )

    def gap_counts(self, days: int = 14) -> list[tuple[str, int, int]]:
        """(gap, jobs, FDE jobs) across eligible jobs still listed in the last `days` days."""
        rows = self.conn.execute(
            """
            SELECT g.gap, count(*) AS jobs, count(*) FILTER (WHERE p.track = 'fde') AS fde
            FROM postings p, jsonb_array_elements_text(p.match->'gaps') AS g(gap)
            WHERE p.eligible AND p.last_seen > now() - make_interval(days => %s)
            GROUP BY g.gap ORDER BY count(*) FILTER (WHERE p.track = 'fde') * 2 + count(*) DESC, g.gap
            """,
            (days,),
        ).fetchall()
        return [(r["gap"], r["jobs"], r["fde"]) for r in rows]

    def forget_job_facts(self) -> None:
        """Drop every verdict and the model's facts, so all postings are read again (after the
        extraction prompt or schema changes)."""
        self.conn.execute("UPDATE postings SET job_facts = NULL, llm_usage = NULL, track = NULL, "
                          "decided_at = NULL, eligible = NULL")

    def forget_rule_verdicts(self) -> None:
        """Drop verdicts made by rules alone, so changed criteria can let those postings through."""
        self.conn.execute("UPDATE postings SET decided_at = NULL WHERE job_facts IS NULL")

    def undigested(self) -> Batch:
        """Eligible postings not yet sent, one per fingerprint, skipping jobs already sent from
        another board; plus why the other newly decided postings were dropped."""
        rows = self.conn.execute(
            """
            SELECT DISTINCT ON (fingerprint)
                   id, source, company, title, url, location, track, posted_at, flags, match
            FROM postings p
            WHERE digested_at IS NULL AND eligible
              AND NOT EXISTS (SELECT 1 FROM postings d
                              WHERE d.fingerprint = p.fingerprint AND d.digested_at IS NOT NULL)
            ORDER BY fingerprint, (source = 'mcf'), first_seen
            """
        ).fetchall()
        rejected = self.conn.execute(
            """
            SELECT id, reasons FROM postings
            WHERE digested_at IS NULL AND decided_at IS NOT NULL AND NOT eligible
            """
        ).fetchall()
        resurfaced = self.conn.execute(
            """
            SELECT id, source, company, title, url, location, track, posted_at, flags, match
            FROM postings WHERE resurface AND eligible AND digested_at IS NOT NULL
            """
        ).fetchall()
        tally: Counter[str] = Counter()
        for r in rejected:
            tally[_reason_kind(r["reasons"][0])] += 1
        return Batch(rows=[Row(**r) for r in rows], rejected=tally,
                     decided_ids=[r["id"] for r in rows] + [r["id"] for r in rejected],
                     resurfaced=[Row(**r) for r in resurfaced])

    def mark_digested(self, batch: Batch) -> None:
        with self.conn.transaction():
            self.conn.execute(
                """
                UPDATE postings SET digested_at = now()
                WHERE (id = ANY(%s)
                       OR fingerprint IN (SELECT fingerprint FROM postings WHERE id = ANY(%s) AND eligible))
                  AND digested_at IS NULL
                """,
                (batch.decided_ids, batch.decided_ids),
            )
            self.conn.execute("UPDATE postings SET resurface = false WHERE id = ANY(%s)",
                              ([r.id for r in batch.resurfaced],))


def _reason_kind(reason: str) -> str:
    """'needs 5+ years' -> 'needs N+ years', so the digest can tally reasons."""
    for kind in ("senior title", "employment", "level", "not FDE or a path", "requires"):
        if reason.startswith(kind):
            return kind
    if reason.startswith("needs"):
        return "too much experience"
    if reason.startswith("pay up to"):
        return "pay below S Pass minimum"
    return reason
