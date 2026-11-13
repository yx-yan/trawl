# trawl

**Cast wide, keep what fits.** trawl collects forward-deployed-engineer (FDE) jobs from
public job boards, works out which ones really are FDE work whatever they're called, tailors
each application, and submits only what a human has approved.

I built it for my own FDE job search, and I built it the way I would deliver it to a client
such as a university career office or a recruiting agency: real integrations, an LLM pipeline
with evals, a human approval queue, an audit log, and a Docker deployment.

> 中文简介：trawl（拖网）是我找 FDE 工作时自己用的系统：从公开招聘接口收集岗位，按 JD 内容判断是不是
> FDE 类工作（不管岗位叫什么），定制简历和申请材料，只有人工批准后才提交。同时按"给企业客户交付"的标准来做。

## Status

| Milestone | What it does | |
|---|---|---|
| M0 | Collect from Greenhouse, Lever, Ashby, MyCareersFuture; daily Telegram digest | ✅ |
| M1 | LLM extraction, FDE classification by description, match scoring, evals | |
| M2 | Approval queue (Next.js) and tailored résumé per job | |
| M3 | Application filler: pre-fill, screenshot, wait for approval, then submit | |
| M4 | Status tracking from Gmail, funnel dashboard | |
| M5 | Multi-tenant, RBAC, PII encryption, tracing, delivery docs | |

## Run it

```bash
docker compose up -d db                  # Postgres (with pgvector)
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env                     # add a Telegram bot token to get the digest on your phone
.venv/bin/trawl collect                  # fetch every board in sources.toml
.venv/bin/trawl digest --dry-run         # print what the digest would send
.venv/bin/trawl run                      # collect + send; this is what cron runs
```

Or entirely in Docker: `docker compose run --rm trawl run`.

Which companies and searches to watch lives in [`sources.toml`](sources.toml); the role names
counted as FDE-like are in [`docs/roles.md`](docs/roles.md).

## Tests

```bash
.venv/bin/pytest                                        # offline: recorded board responses
TRAWL_TEST_DATABASE_URL=postgresql://trawl:trawl@localhost:5432/trawl .venv/bin/pytest   # + Postgres
.venv/bin/ruff check .
```

## Design

See [`docs/design.md`](docs/design.md).
