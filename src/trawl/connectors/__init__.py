"""Connectors: one module per job board, each turning the board's public API into Postings.

Every connector is a plain function `fetch(http, key) -> list[Posting]`, where `key` is the
company's board name (or, for MyCareersFuture, a search query). They only read public endpoints.
Boards whose lists carry no descriptions also have `describe(http, external_id, facts)`, which
`process` calls only for postings that survive the first rules.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from ..models import Posting
from . import ashby, greenhouse, lever, mcf, smartrecruiters, workday

Fetch = Callable[[httpx.Client, str], list[Posting]]
Describe = Callable[[httpx.Client, str, dict], tuple[str, dict]]

CONNECTORS: dict[str, Fetch] = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "workday": workday.fetch,
    "smartrecruiters": smartrecruiters.fetch,
    "mcf": mcf.fetch,
}

DESCRIBE: dict[str, Describe] = {
    "mcf": lambda http, external_id, _facts: mcf.describe(http, external_id),
    "workday": workday.describe,
    "smartrecruiters": smartrecruiters.describe,
}
