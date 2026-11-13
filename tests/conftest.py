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
