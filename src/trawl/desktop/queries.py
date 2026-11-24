"""What the desktop app shows: the jobs, one job in full, and the funnel.

A job's status is the latest stage recorded for it (applied, interview, offer, rejected, withdrawn,
skipped), else "prepared" when a tailored application exists, else "new". The same job posted on two
boards shows once, as the digest does: the copy with a stage, else the company's own board.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import psycopg

from ..config import HOME

OPEN_DAYS = 3  # a posting the collector hasn't seen for this long has closed
FILLABLE = ("greenhouse", "ashby", "lever")  # boards `trawl apply` can fill

JOBS = f"""
SELECT DISTINCT ON (p.fingerprint)
       p.id, p.source, p.company, p.title, p.url, p.location, p.track, p.flags,
       (p.match->>'score')::int AS score, p.match->'strengths' AS strengths, p.match->'gaps' AS gaps,
       p.first_seen, p.last_seen, p.last_seen > now() - interval '{OPEN_DAYS} days' AS open,
       a.status AS app_status, a.created_at AS prepared_at, st.stage, st.at AS stage_at,
       coalesce(jsonb_path_exists(p.leads, '$[*] ? (@.kind != "search")'), false) AS has_leads
FROM postings p
LEFT JOIN LATERAL (SELECT status, created_at FROM applications WHERE posting_id = p.id
                   ORDER BY created_at DESC LIMIT 1) a ON true
LEFT JOIN LATERAL (SELECT stage, at FROM stages WHERE posting_id = p.id
                   ORDER BY at DESC, id DESC LIMIT 1) st ON true
WHERE p.eligible OR st.stage IS NOT NULL
ORDER BY p.fingerprint, (st.stage IS NULL), (a.status IS NULL), (p.source = 'mcf'), p.first_seen
"""


def status(row: dict) -> str:
    if row.get("stage"):
        return row["stage"]
    return "prepared" if row.get("app_status") else "new"


def jobs(conn: psycopg.Connection) -> list[dict]:
    out = []
    for r in conn.execute(JOBS).fetchall():
        out.append(r | {"status": status(r), "fillable": r["source"] in FILLABLE,
                        "strengths": r["strengths"] or [], "gaps": r["gaps"] or []})
    out.sort(key=lambda r: (not r["open"], -(r["score"] if r["score"] is not None else -1),
                            r["company"].lower()))
    return out


def job(conn: psycopg.Connection, posting_id: int) -> dict[str, Any] | None:
    p = conn.execute(
        """SELECT id, source, external_id, company, title, url, location, track, flags, reasons, match,
                  job_facts, first_seen, last_seen, last_seen > now() - %s * interval '1 day' AS open,
                  posted_at, eligible, leads, leads_at, requirements
           FROM postings WHERE id = %s""", (OPEN_DAYS, posting_id)).fetchone()
    if p is None:
        return None
    app = conn.execute(
        """SELECT source_hash, status, tailoring, answers, created_at, submitted_at,
                  length(resume_pdf) > 0 AS has_pdf
           FROM applications WHERE posting_id = %s ORDER BY created_at DESC LIMIT 1""",
        (posting_id,)).fetchone()
    stages = conn.execute("SELECT id, stage, at, note, by_trawl FROM stages WHERE posting_id = %s "
                          "ORDER BY at, id",
                          (posting_id,)).fetchall()
    audit = conn.execute("SELECT at, event, detail FROM audit_log WHERE posting_id = %s ORDER BY at, id",
                         (posting_id,)).fetchall()
    latest = {"stage": stages[-1]["stage"] if stages else None, "app_status": app and app["status"]}
    if app:
        t = app.pop("tailoring") or {}
        app["edits"] = t.get("edits", [])
        app["changes"] = (t.get("tailoring") or {}).get("changes", [])
        app["notes"] = t.get("notes", [])
        app["projects"] = t.get("projects", [])
        app["chosen"] = t.get("chosen", False)
    return p | {"status": status(latest), "fillable": p["source"] in FILLABLE, "application": app,
                "stages": stages, "audit": audit, "screenshots": [s.name for s in screenshots(posting_id)]}


def screenshots(posting_id: int, home: Path = HOME) -> list[Path]:
    """The screenshots `trawl apply` took for a job, oldest first."""
    folders = (home / "applications").glob(f"{posting_id}-*")
    shots = (f for d in folders if d.is_dir() for f in d.glob("*.png"))
    return sorted(shots, key=lambda f: f.name.split("-", 1)[1])  # by the time in the name


SAFE_NAME = re.compile(r"^[\w.-]+\.png$")


def screenshot(posting_id: int, name: str, home: Path = HOME) -> Path | None:
    if not SAFE_NAME.match(name):
        return None
    return next((s for s in screenshots(posting_id, home) if s.name == name), None)


def resume_pdf(conn: psycopg.Connection, posting_id: int) -> bytes | None:
    row = conn.execute("SELECT resume_pdf FROM applications WHERE posting_id = %s ORDER BY created_at DESC "
                       "LIMIT 1", (posting_id,)).fetchone()
    return bytes(row["resume_pdf"]) if row else None


def stats(conn: psycopg.Connection) -> dict[str, Any]:
    """The funnel (each step counts distinct jobs, one per fingerprint), applications by board, and
    applications per week."""
    one = conn.execute(
        """
        WITH reached AS (SELECT p.fingerprint, s.stage FROM stages s JOIN postings p ON p.id = s.posting_id)
        SELECT (SELECT count(DISTINCT fingerprint) FROM postings) AS collected,
               (SELECT count(DISTINCT fingerprint) FROM postings WHERE eligible) AS eligible,
               (SELECT count(DISTINCT p.fingerprint) FROM applications a
                JOIN postings p ON p.id = a.posting_id) AS prepared,
               (SELECT count(DISTINCT fingerprint) FROM reached WHERE stage = 'applied') AS applied,
               (SELECT count(DISTINCT fingerprint) FROM reached WHERE stage = 'interview') AS interview,
               (SELECT count(DISTINCT fingerprint) FROM reached WHERE stage = 'offer') AS offer,
               (SELECT count(DISTINCT fingerprint) FROM reached WHERE stage = 'rejected') AS rejected
        """).fetchone()
    by_source = conn.execute(
        """SELECT p.source, count(DISTINCT p.fingerprint) FILTER (WHERE p.eligible) AS eligible,
                  count(DISTINCT p.fingerprint) FILTER (WHERE s.stage = 'applied') AS applied
           FROM postings p LEFT JOIN stages s ON s.posting_id = p.id
           GROUP BY p.source ORDER BY eligible DESC""").fetchall()
    weekly = conn.execute(
        """SELECT date_trunc('week', at)::date AS week, count(*) AS applied FROM stages
           WHERE stage = 'applied' AND at > now() - interval '12 weeks' GROUP BY 1 ORDER BY 1""").fetchall()
    return {"funnel": one, "by_source": by_source, "weekly": weekly}
