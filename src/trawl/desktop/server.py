"""The desktop app's API, for its own page only: it listens on 127.0.0.1.

Reads go straight to the database. The person's own records (an application sent from another
site, an interview, a rejection) are stages; removing one is an undo.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import Settings
from ..store import STAGES, Store
from . import queries

STATIC = Path(__file__).parent / "static"


class NewStage(BaseModel):
    stage: str
    note: str = ""


def create_app(store: Store, settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="trawl", docs_url=None, redoc_url=None)
    conn = store.conn

    @app.get("/api/jobs")
    def jobs() -> list[dict]:
        return queries.jobs(conn)

    @app.get("/api/jobs/{posting_id}")
    def job(posting_id: int) -> dict:
        found = queries.job(conn, posting_id)
        if found is None:
            raise HTTPException(404, "no such job")
        return found

    @app.get("/api/jobs/{posting_id}/resume.pdf")
    def resume(posting_id: int) -> Response:
        pdf = queries.resume_pdf(conn, posting_id)
        if pdf is None:
            raise HTTPException(404, "no tailored résumé")
        return Response(pdf, media_type="application/pdf")

    @app.get("/api/jobs/{posting_id}/screenshots/{name}")
    def screenshot(posting_id: int, name: str) -> FileResponse:
        path = queries.screenshot(posting_id, name)
        if path is None:
            raise HTTPException(404, "no such screenshot")
        return FileResponse(path, media_type="image/png")

    @app.post("/api/jobs/{posting_id}/stages")
    def add_stage(posting_id: int, body: NewStage) -> dict:
        if body.stage not in STAGES:
            raise HTTPException(400, f"stage must be one of {', '.join(STAGES)}")
        if store.posting(posting_id) is None:
            raise HTTPException(404, "no such job")
        return {"id": store.add_stage(posting_id, body.stage, body.note.strip()[:500])}

    @app.delete("/api/stages/{stage_id}")
    def remove_stage(stage_id: int) -> dict:
        if not store.remove_stage(stage_id):
            raise HTTPException(400, "only a stage you recorded can be undone")
        return {"ok": True}

    @app.get("/api/stats")
    def stats() -> dict:
        return queries.stats(conn)

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
