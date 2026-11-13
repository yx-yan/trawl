"""Give every new posting a verdict, cheapest checks first.

1. Rules on the title and board facts: most postings stop here, with no network or model call.
2. MyCareersFuture postings fetch their detail (description, agency code, years), then the rules
   run again with the new facts.
3. The model reads what's left; rules turn its facts into the verdict.
4. Eligible jobs are scored against the résumé, and scored again when the résumé changes.

Network and model calls run in a thread pool; all database writes stay on the calling thread.
A posting whose call fails keeps no verdict and is retried on the next run.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx

from .connectors import mcf
from .decide import Criteria, Verdict, decide, prefilter
from .extract import Client, JobFacts, extract_with_retry, with_retry
from .match import RISE, match
from .store import Pending, Store

WORKERS = 8


@dataclass
class Stats:
    rules: int = 0  # rejected before any model call
    read: int = 0  # read by the model
    eligible: int = 0
    matched: int = 0  # eligible jobs scored against the résumé
    failed: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    def __str__(self) -> str:
        return (f"{self.rules} rejected by rules, {self.read} read by the model, {self.eligible} eligible, "
                f"{self.matched} scored against the résumé, {len(self.failed)} failed; "
                f"{self.input_tokens} in / {self.output_tokens} out tokens")


def _parallel[I, T](fn: Callable[[I], T], items: list[I]) -> list[tuple[I, T | Exception]]:
    def safe(p: I) -> T | Exception:
        try:
            return fn(p)
        except Exception as e:  # one bad posting must not stop the batch
            return e

    with ThreadPoolExecutor(WORKERS) as pool:
        return list(zip(items, pool.map(safe, items), strict=True))


def process(store: Store, criteria: Criteria, http: httpx.Client, client: Client,
            resume: tuple[str, str] | None = None) -> Stats:
    """`resume` is (text, hash) from match.load_resume; without it, jobs are judged but not scored."""
    stats = Stats()

    def reject_by_rules(items: list[Pending]) -> list[Pending]:
        left = []
        for p in items:
            if reasons := prefilter(p.title, p.facts, criteria):
                store.save_verdict(p.id, Verdict(eligible=False, reasons=reasons))
                stats.rules += 1
            else:
                left.append(p)
        return left

    left = reject_by_rules(store.pending())

    # 2. details for MyCareersFuture postings, which arrive without a description
    bare = [p for p in left if p.source == "mcf" and not p.description]
    for p, result in _parallel(lambda p: mcf.describe(http, p.external_id), bare):
        if isinstance(result, Exception):
            stats.failed.append(f"{p.company}: {p.title}: detail: {result}")
            left.remove(p)
            continue
        p.description, extra = result
        p.facts = p.facts | extra
        store.save_details(p.id, p.description, extra)
    left = reject_by_rules(left)

    # 3. the model reads the rest
    def read(p: Pending) -> tuple[JobFacts, dict]:
        job, usage = extract_with_retry(client, company=p.company, title=p.title, location=p.location,
                                        description=p.description)
        return job, {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                     "seconds": round(usage.seconds, 2)}

    for p, result in _parallel(read, left):
        if isinstance(result, Exception):
            stats.failed.append(f"{p.company}: {p.title}: model: {result}")
            continue
        job, usage = result
        verdict = decide(p.title, p.facts, job, criteria)
        store.save_verdict(p.id, verdict, job_facts=job.model_dump(), usage=usage)
        stats.read += 1
        stats.eligible += verdict.eligible
        stats.input_tokens += usage["input_tokens"]
        stats.output_tokens += usage["output_tokens"]

    # 4. score eligible jobs against the résumé, again whenever the résumé changes
    if resume:
        text, resume_hash = resume
        todo = store.to_match(resume_hash)
        scored = _parallel(lambda m: with_retry(lambda: match(client, text, company=m.company, title=m.title,
                                                               description=m.description)), todo)
        for m, result in scored:
            if isinstance(result, Exception):
                stats.failed.append(f"{m.company}: {m.title}: match: {result}")
                continue
            new, usage = result
            old = (m.match or {}).get("score")
            rose = m.digested and old is not None and new.score - old >= RISE
            store.save_match(m.id, new.model_dump(), resume_hash, resurface=rose)
            stats.matched += 1
            stats.input_tokens += usage.input_tokens
            stats.output_tokens += usage.output_tokens

    for line in stats.failed:
        print(f"  failed: {line}", file=sys.stderr)
    return stats


def redecide(store: Store, criteria: Criteria) -> int:
    """Apply changed criteria to everything the model has read, without calling it again.
    Postings rejected by rules alone get no verdict and go through `process` again."""
    count = 0
    for p in store.decided_with_facts():
        store.save_verdict(p.id, decide(p.title, p.facts, JobFacts.model_validate(p.job_facts), criteria))
        count += 1
    store.forget_rule_verdicts()
    return count
