"""Live eval of job-fact extraction: real postings, facts checked by hand against the text.

Each case in cases.json is a snapshot of a real posting (descriptions change and postings close),
the facts a careful reader gets from it, and whether the default criteria should let it through.
This calls the model, so it costs money (about 15 calls).

    python evals/run_evals.py            # all cases
    python evals/run_evals.py Palantir   # cases whose company or title contains this
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from trawl.config import load_dotenv
from trawl.decide import Criteria, decide
from trawl.extract import extract_with_retry, new_client

HERE = Path(__file__).parent


def check(case: dict, client: object) -> dict:
    job, usage = extract_with_retry(client, company=case["company"], title=case["title"],
                                    location=case["location"], description=case["description"])
    facts = job.model_dump()
    misses = {}
    for name, want in case["expect"].items():
        ok = facts[name] in want if isinstance(want, list) else facts[name] == want
        if not ok:
            misses[name] = {"want": want, "got": facts[name]}
    verdict = decide(case["title"], case["board_facts"], job, Criteria())
    if verdict.eligible != case["eligible"]:
        misses["eligible"] = {"want": case["eligible"], "got": verdict.eligible, "reasons": verdict.reasons}
    return {"company": case["company"], "title": case["title"], "misses": misses, "facts": facts,
            "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}


def main() -> None:
    load_dotenv()
    cases = json.loads((HERE / "cases.json").read_text())
    if len(sys.argv) > 1:
        cases = [c for c in cases if sys.argv[1].lower() in (c["company"] + c["title"]).lower()]
    client = new_client()
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda c: check(c, client), cases))

    checks = sum(len(c["expect"]) + 1 for c in cases)
    failed = sum(len(r["misses"]) for r in results)
    for r in results:
        mark = "ok  " if not r["misses"] else "FAIL"
        print(f"{mark} {r['company']}: {r['title']}")
        for name, miss in r["misses"].items():
            print(f"       {name}: {miss}")
    tokens = sum(r["input_tokens"] for r in results), sum(r["output_tokens"] for r in results)
    print(f"\n{checks - failed}/{checks} checks passed, "
          f"{sum(not r['misses'] for r in results)}/{len(results)} cases; "
          f"{tokens[0]} in / {tokens[1]} out tokens")

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / f"{datetime.now():%Y%m%d-%H%M%S}.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
