"""Connectors: one module per job board, each turning the board's public API into Postings.

Every connector is a plain function `fetch(http, key) -> list[Posting]`, where `key` is the
company's board name (or, for MyCareersFuture, a search query). They only read public endpoints.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from ..models import Posting
from . import ashby, greenhouse, lever, mcf

Fetch = Callable[[httpx.Client, str], list[Posting]]

CONNECTORS: dict[str, Fetch] = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "mcf": mcf.fetch,
}
