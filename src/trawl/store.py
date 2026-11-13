"""Postgres storage for postings.

Every posting in a wanted location is kept, not only title matches: from M1 on, roles are
classified by description, and the FDE-like ones with unhelpful titles must already be here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .models import Posting
from .titles import title_tier

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
    title_tier  TEXT,
    fingerprint TEXT NOT NULL,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    digested_at TIMESTAMPTZ,
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS postings_fingerprint ON postings (fingerprint);
"""


@dataclass
class Row:
    id: int
    source: str
    company: str
    title: str
    url: str
    location: str
    title_tier: str | None
    posted_at: datetime | None


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
                                          description, posted_at, title_tier, fingerprint)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source, external_id) DO UPDATE SET
                        title = EXCLUDED.title, url = EXCLUDED.url, location = EXCLUDED.location,
                        description = CASE WHEN EXCLUDED.description <> ''
                                           THEN EXCLUDED.description ELSE postings.description END,
                        title_tier = EXCLUDED.title_tier, fingerprint = EXCLUDED.fingerprint,
                        last_seen = now()
                    RETURNING (xmax = 0) AS inserted
                    """,
                    (p.source, p.external_id, p.company, p.title, p.url, p.location,
                     p.description, p.posted_at, title_tier(p.title), p.fingerprint),
                ).fetchone()
                new += bool(row and row["inserted"])
        return new

    def undigested(self) -> list[Row]:
        """Title-matched postings not yet sent, one per fingerprint, skipping jobs already sent
        from another board."""
        rows = self.conn.execute(
            """
            SELECT DISTINCT ON (fingerprint)
                   id, source, company, title, url, location, title_tier, posted_at
            FROM postings p
            WHERE digested_at IS NULL AND title_tier IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM postings d
                              WHERE d.fingerprint = p.fingerprint AND d.digested_at IS NOT NULL)
            ORDER BY fingerprint, (source = 'mcf'), first_seen
            """
        ).fetchall()
        return [Row(**r) for r in rows]

    def mark_digested(self, rows: Iterable[Row]) -> None:
        ids = [r.id for r in rows]
        with self.conn.transaction():
            self.conn.execute(
                """
                UPDATE postings SET digested_at = now()
                WHERE fingerprint IN (SELECT fingerprint FROM postings WHERE id = ANY(%s))
                  AND digested_at IS NULL
                """,
                (ids,),
            )
