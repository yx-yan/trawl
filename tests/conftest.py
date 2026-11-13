"""Test helpers: job-board responses recorded from the real APIs, served offline."""

from __future__ import annotations

import functools
import json
import os
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> object:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture
def board_http() -> httpx.Client:
    """An httpx client whose transport answers each board's API from tests/fixtures."""

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "boards-api.greenhouse.io":
            return httpx.Response(200, json=fixture("greenhouse"))
        if host == "api.lever.co":
            if request.url.path.endswith("/nope"):
                return httpx.Response(200, json={"ok": False, "error": "Document not found"})
            return httpx.Response(200, json=fixture("lever"))
        if host == "api.ashbyhq.com":
            return httpx.Response(200, json=fixture("ashby"))
        if host == "api.mycareersfuture.gov.sg":
            if request.url.path.startswith("/v2/jobs/"):
                return httpx.Response(200, json=fixture("mcf_detail"))
            return httpx.Response(200, json=fixture("mcf"))
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def store() -> Iterator[object]:
    """A Store on a throwaway schema; needs TRAWL_TEST_DATABASE_URL (e.g. the compose db)."""
    url = os.environ.get("TRAWL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("TRAWL_TEST_DATABASE_URL not set")
    import psycopg

    from trawl.store import Store

    schema = f"test_{os.getpid()}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.execute(f"CREATE SCHEMA {schema}")
    connect = functools.partial(Store.connect, url, options=f"-c search_path={schema}")
    store = connect()
    store.reconnect = connect  # a second, independent connection, to check what was committed
    yield store
    store.conn.close()
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")


class FakeClient:
    """Stands in for anthropic.Anthropic: answers every request with the given tool input."""

    def __init__(self, *answers: dict) -> None:
        self.answers = list(answers)
        self.requests: list[dict] = []
        self.messages = self

    def create(self, **kwargs: object) -> object:
        from types import SimpleNamespace

        self.requests.append(kwargs)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", input=answer)], stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=80))


def job_facts(**overrides: object) -> dict:
    """Facts for an eligible new-grad FDE job; override what a test is about."""
    return {"track": "fde", "seniority": "entry", "min_years": None, "employment": "permanent",
            "agency": False, "work_authorization": "unspecified", "based_in_singapore": True,
            "graduation_from": None, "graduation_to": None, "requires_phd": False,
            "required_languages": [], "writes_code": True, "intake": None, "reason": "FDE role"} | overrides
