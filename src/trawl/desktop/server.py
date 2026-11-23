"""The desktop app's API, for its own page only: it listens on 127.0.0.1.

Reads go straight to the database. The person's own records (an application sent from another
site, an interview, a rejection) are stages; removing one is an undo. Preparing (tailoring and
drafting, a model call) and applying (Chrome, with the person approving on the page) run as tasks
the page follows (`tasks.py`).
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
from .tasks import Busy, Task, Tasks

STATIC = Path(__file__).parent / "static"


class NewStage(BaseModel):
    stage: str
    note: str = ""


class Prepare(BaseModel):
    again: bool = False


class Apply(BaseModel):
    submit: bool = True  # False: fill the form to look at, never submit


class Reply(BaseModel):
    reply: str = ""


def create_app(store: Store, settings: Settings | None = None, tasks: Tasks | None = None) -> FastAPI:
    app = FastAPI(title="trawl", docs_url=None, redoc_url=None)
    conn = store.conn
    settings = settings or Settings.from_env()
    tasks = tasks or Tasks()

    @app.get("/api/jobs")
    def jobs() -> list[dict]:
        return queries.jobs(conn)

    @app.get("/api/jobs/{posting_id}")
    def job(posting_id: int) -> dict:
        found = queries.job(conn, posting_id)
        if found is None:
            raise HTTPException(404, "no such job")
        task = tasks.active(posting_id)
        return found | {"task": task.id if task else None}

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

    def start(posting_id: int, kind: str, work) -> dict:  # noqa: ANN001
        if store.posting(posting_id) is None:
            raise HTTPException(404, "no such job")
        try:
            return tasks.start(posting_id, kind, work).view()
        except Busy as e:
            raise HTTPException(409, str(e)) from None

    @app.post("/api/jobs/{posting_id}/prepare")
    def prepare(posting_id: int, body: Prepare) -> dict:
        return start(posting_id, "prepare",
                     lambda task: prepare_work(store, settings, posting_id, task, body.again))

    @app.post("/api/jobs/{posting_id}/apply")
    def apply(posting_id: int, body: Apply) -> dict:
        return start(posting_id, "apply",
                     lambda task: apply_work(store, settings, posting_id, task, body.submit))

    @app.get("/api/tasks/{task_id}")
    def task(task_id: int) -> dict:
        found = tasks.get(task_id)
        if found is None:
            raise HTTPException(404, "no such task")
        return found.view()

    @app.post("/api/tasks/{task_id}/reply")
    def reply(task_id: int, body: Reply) -> dict:
        if not tasks.reply(task_id, body.reply):
            raise HTTPException(409, "that task isn't waiting for an answer")
        return {"ok": True}

    @app.post("/api/jobs/{posting_id}/leads")
    def find_leads(posting_id: int) -> dict:
        from .. import where

        if store.posting(posting_id) is None:
            raise HTTPException(404, "no such job")
        with where.client() as http:
            return {"leads": where.find(store, http, posting_id)}

    @app.get("/api/stats")
    def stats() -> dict:
        return queries.stats(conn)

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


def prepare_work(store: Store, settings: Settings, posting_id: int, task: Task, again: bool) -> str:
    """Tailor the résumé and draft the form, as `trawl tailor <id>` does."""
    import httpx

    from .. import digest
    from ..cli import USER_AGENT, load_profile, load_tailoring
    from ..extract import new_client
    from ..process import prepare_one

    loaded = load_tailoring(settings)
    if loaded is None:
        raise RuntimeError("no résumé or résumé class in ~/.trawl: run "
                           "`TRAWL_DEPLOY_HOST=local deploy/deploy.sh profile` in the checkout")
    task.say("Tailoring the résumé and drafting the form…")
    with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}) as http:
        app, note = prepare_one(store, new_client, loaded, load_profile(settings), http, posting_id,
                                again=again)
    task.say(digest.caption(app, limit=10_000))
    if note:
        task.say(f"Form: {note}")
    elif app.answers is None:
        task.say("No form to draft on this board.")
    return "prepared"


def apply_work(store: Store, settings: Settings, posting_id: int, task: Task, submit: bool) -> str:
    """Fill the form in Chrome and, when the person approves on the page, submit it."""
    from ..apply import run
    from ..cli import load_profile

    return run(store, settings, posting_id, load_profile(settings), submit=submit, ask=task.ask, say=task.say)
