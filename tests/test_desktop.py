"""The desktop app's API over a real database (needs TRAWL_TEST_DATABASE_URL), and its file guards."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from trawl.desktop import queries  # noqa: E402
from trawl.desktop.server import create_app  # noqa: E402
from trawl.models import Posting  # noqa: E402
from trawl.store import Store  # noqa: E402


def seed(store: Store) -> dict[str, int]:
    """Three jobs: one on two boards (Greenhouse and MyCareersFuture), one prepared, one bare."""
    store.upsert([
        Posting("greenhouse", "1", "Example", "Forward Deployed Engineer", "https://example.com/1",
                "Singapore", None),
        Posting("mcf", "m1", "EXAMPLE PTE. LTD.", "Forward Deployed Engineer", "https://mcf/1",
                "Singapore", None),
        Posting("ashby", "2", "Other", "Agent Engineer", "https://example.com/2", "Singapore", None),
        Posting("lever", "3", "Third", "Solutions Engineer", "https://example.com/3", "Singapore", None),
    ])
    ids = {r["source"]: r["id"] for r in store.conn.execute("SELECT id, source FROM postings").fetchall()}
    store.conn.execute("UPDATE postings SET eligible = true, match = '{\"score\": 70}'")
    store.save_application(ids["ashby"], "h", {"edits": ["summary rewritten"]}, "tex", b"%PDF-1.4", [])
    return ids


@pytest.fixture
def client(store) -> TestClient:  # noqa: ANN001
    return TestClient(create_app(store))


def test_jobs_show_each_job_once_with_its_status(store, client: TestClient) -> None:  # noqa: ANN001
    ids = seed(store)
    jobs = client.get("/api/jobs").json()
    assert sorted((j["source"], j["status"]) for j in jobs) == [
        ("ashby", "prepared"), ("greenhouse", "new"), ("lever", "new")]  # the MCF copy folds into Greenhouse
    assert {j["id"]: j["fillable"] for j in jobs} == {ids["greenhouse"]: True, ids["ashby"]: True,
                                                       ids["lever"]: True}


def test_a_stage_on_the_mcf_copy_shows_that_copy(store, client: TestClient) -> None:  # noqa: ANN001
    ids = seed(store)
    added = client.post(f"/api/jobs/{ids['mcf']}/stages", json={"stage": "applied", "note": "via MCF"})
    assert added.is_success
    by_id = {j["id"]: j for j in client.get("/api/jobs").json()}
    assert by_id[ids["mcf"]]["status"] == "applied" and ids["greenhouse"] not in by_id


def test_job_detail_and_stages(store, client: TestClient) -> None:  # noqa: ANN001
    ids = seed(store)
    job = client.get(f"/api/jobs/{ids['ashby']}").json()
    assert job["status"] == "prepared" and job["application"]["edits"] == ["summary rewritten"]
    assert client.get(f"/api/jobs/{ids['ashby']}/resume.pdf").content == b"%PDF-1.4"
    assert client.get("/api/jobs/999999").status_code == 404

    assert client.post(f"/api/jobs/{ids['ashby']}/stages", json={"stage": "hired"}).status_code == 400
    added = client.post(f"/api/jobs/{ids['ashby']}/stages", json={"stage": "interview", "note": "call"})
    sid = added.json()["id"]
    job = client.get(f"/api/jobs/{ids['ashby']}").json()
    assert job["status"] == "interview" and job["stages"][0]["note"] == "call"
    assert client.delete(f"/api/stages/{sid}").is_success
    assert client.get(f"/api/jobs/{ids['ashby']}").json()["status"] == "prepared"


def test_a_submission_trawl_made_is_a_stage_that_cant_be_undone(store, client: TestClient) -> None:  # noqa: ANN001
    ids = seed(store)
    store.mark_submitted(ids["ashby"], "h")
    [stage] = client.get(f"/api/jobs/{ids['ashby']}").json()["stages"]
    assert (stage["stage"], stage["by_trawl"]) == ("applied", True)
    assert client.delete(f"/api/stages/{stage['id']}").status_code == 400
    # reconnecting runs the schema again: the backfill adds no second "applied"
    again = store.reconnect()
    assert again.conn.execute("SELECT count(*) AS n FROM stages").fetchone()["n"] == 1


def test_stats_count_jobs_not_postings(store, client: TestClient) -> None:  # noqa: ANN001
    ids = seed(store)
    store.mark_submitted(ids["ashby"], "h")
    store.add_stage(ids["ashby"], "interview")
    funnel = client.get("/api/stats").json()["funnel"]
    assert funnel == {"collected": 3, "eligible": 3, "prepared": 1, "applied": 1, "interview": 1, "offer": 0,
                      "rejected": 0}


def test_screenshots_stay_in_their_folder(tmp_path: Path) -> None:
    folder = tmp_path / "applications" / "7-example"
    folder.mkdir(parents=True)
    (folder / "submitted-20261123-120000.png").write_bytes(b"png")
    (folder / "approved-20261123-115900.png").write_bytes(b"png")
    (tmp_path / "secret.png").write_bytes(b"no")
    assert [p.name for p in queries.screenshots(7, tmp_path)] == ["approved-20261123-115900.png",
                                                                  "submitted-20261123-120000.png"]
    assert queries.screenshot(7, "submitted-20261123-120000.png", tmp_path) is not None
    assert queries.screenshot(7, "../../secret.png", tmp_path) is None
    assert queries.screenshot(8, "submitted-20261123-120000.png", tmp_path) is None


# --- tasks: preparing and applying, the page answering what trawl asks --------------------------------

from trawl.desktop import server  # noqa: E402
from trawl.desktop.tasks import Busy, Tasks, prompt_for  # noqa: E402


def test_prompt_for_turns_trawls_questions_into_choices() -> None:
    approve = prompt_for("\nFinish the form in Chrome and check every field. "
                         "Type 'submit' to send it, or press Enter to stop: ")
    assert approve["kind"] == "approve"
    assert approve["text"] == "Finish the form in Chrome and check every field."
    assert [c["reply"] for c in approve["choices"]] == ["submit", ""]
    assert prompt_for("Does the page say it was submitted? [y/N] ")["kind"] == "yesno"
    close = prompt_for("\nLook it over in Chrome; nothing will be submitted. Press Enter to close. ")
    assert close["kind"] == "close"


def wait(tasks: Tasks, task_id: int, state: str, timeout: float = 5) -> dict:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = tasks.get(task_id)
        if t.state == state:
            return t.view()
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} is {tasks.get(task_id).state}, not {state}")


def test_tasks_wait_for_the_page_and_allow_one_application_at_a_time() -> None:
    tasks = Tasks()

    def work(task):  # noqa: ANN001, ANN202
        task.say("filled 3 fields")
        return "submitted" if task.ask("Type 'submit' to send it") == "submit" else "stopped"

    t = tasks.start(1, "apply", work)
    wait(tasks, t.id, "waiting")
    with pytest.raises(Busy):
        tasks.start(1, "prepare", lambda _: "x")  # one task per job
    with pytest.raises(Busy):
        tasks.start(2, "apply", lambda _: "x")  # one Chrome window
    assert tasks.start(2, "prepare", lambda _: "prepared")  # preparing another job is fine
    assert tasks.reply(t.id, "submit")
    done = wait(tasks, t.id, "done")
    assert (done["result"], done["messages"], done["prompt"]) == ("submitted", ["filled 3 fields"], None)
    assert not tasks.reply(t.id, "submit")  # nothing waiting any more


def test_a_failing_task_shows_why() -> None:
    tasks = Tasks()

    def work(_):  # noqa: ANN001, ANN202
        raise RuntimeError("the form requires a GPA")

    t = tasks.start(1, "apply", work)
    assert wait(tasks, t.id, "failed")["error"] == "the form requires a GPA"


def test_apply_through_the_api(store, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    ids = seed(store)

    def fake_apply(store, settings, posting_id, task, submit):  # noqa: ANN001, ANN202
        task.say(f"#{posting_id} filled")
        reply = task.ask("Finish the form in Chrome. Type 'submit' to send it, or press Enter to stop: ")
        return "submitted" if submit and reply == "submit" else "stopped"

    monkeypatch.setattr(server, "apply_work", fake_apply)
    tasks = Tasks()
    client = TestClient(create_app(store, tasks=tasks))
    t = client.post(f"/api/jobs/{ids['ashby']}/apply", json={"submit": True}).json()
    wait(tasks, t["id"], "waiting")
    assert client.get(f"/api/jobs/{ids['ashby']}").json()["task"] == t["id"]
    view = client.get(f"/api/tasks/{t['id']}").json()
    assert view["prompt"]["kind"] == "approve" and view["messages"] == [f"#{ids['ashby']} filled"]
    assert client.post(f"/api/jobs/{ids['lever']}/apply", json={}).status_code == 409  # one Chrome window
    assert client.post(f"/api/tasks/{t['id']}/reply", json={"reply": "submit"}).is_success
    assert wait(tasks, t["id"], "done")["result"] == "submitted"
    assert client.post(f"/api/tasks/{t['id']}/reply", json={"reply": "submit"}).status_code == 409
    assert client.post("/api/jobs/999999/prepare", json={}).status_code == 404
