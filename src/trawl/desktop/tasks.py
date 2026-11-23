"""Work the app starts and the page follows: preparing an application, and applying.

Each task runs in a thread of its own (Playwright's sync API stays in the thread that started it)
and keeps what it said and what it's waiting for. Applying asks the person things, the same
questions `trawl apply` asks in a terminal; here each question becomes a prompt on the page, and the
task waits until the page answers. One task per job at a time, and one application at a time: there
is one Chrome window to watch.
"""

from __future__ import annotations

import itertools
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# What trawl apply asks, by the words it asks with, and the answers the page offers for each
PROMPTS = [
    ("Type 'submit'", "approve", [("submit", "Submit"), ("", "Stop")]),
    ("[y/N]", "yesno", [("y", "Yes"), ("n", "No")]),
    ("Press Enter to close", "close", [("", "Close the browser")]),
]


def prompt_for(text: str) -> dict:
    for marker, kind, choices in PROMPTS:
        if marker in text:
            return {"kind": kind, "text": text.split(marker)[0].strip().rstrip(".") + ".",
                    "choices": [{"reply": r, "label": label} for r, label in choices]}
    return {"kind": "text", "text": text.strip(), "choices": [{"reply": "", "label": "Continue"}]}


@dataclass
class Task:
    id: int
    posting_id: int
    kind: str  # prepare, apply
    state: str = "running"  # running, waiting (for the person), done, failed
    messages: list[str] = field(default_factory=list)
    prompt: dict | None = None
    result: str | None = None
    error: str | None = None
    replies: queue.Queue = field(default_factory=queue.Queue, repr=False)

    @property
    def active(self) -> bool:
        return self.state in ("running", "waiting")

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "posting_id": self.posting_id, "kind": self.kind, "state": self.state,
                "messages": self.messages, "prompt": self.prompt, "result": self.result, "error": self.error}

    def say(self, text: str) -> None:
        self.messages.extend(text.splitlines() or [""])

    def ask(self, text: str) -> str:
        self.prompt, self.state = prompt_for(text), "waiting"
        reply = self.replies.get()
        self.prompt, self.state = None, "running"
        return reply


class Busy(Exception):
    pass


class Tasks:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[int, Task] = {}
        self._ids = itertools.count(1)

    def start(self, posting_id: int, kind: str, work: Callable[[Task], str]) -> Task:
        with self._lock:
            if self.active(posting_id):
                raise Busy(f"#{posting_id} already has a task running")
            if kind == "apply" and any(t.kind == "apply" and t.active for t in self._tasks.values()):
                raise Busy("another application is open in Chrome: finish it first")
            task = Task(next(self._ids), posting_id, kind)
            self._tasks[task.id] = task
        threading.Thread(target=self._run, args=(task, work), daemon=True).start()
        return task

    def _run(self, task: Task, work: Callable[[Task], str]) -> None:
        try:
            task.result = work(task)
            task.state = "done"
        except Exception as e:  # noqa: BLE001 - whatever went wrong is shown on the page
            task.error = str(e) or type(e).__name__
            task.state = "failed"

    def get(self, task_id: int) -> Task | None:
        return self._tasks.get(task_id)

    def active(self, posting_id: int) -> Task | None:
        return next((t for t in self._tasks.values() if t.posting_id == posting_id and t.active), None)

    def reply(self, task_id: int, reply: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.state != "waiting":
            return False
        task.replies.put(reply)
        return True
