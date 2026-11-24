<p align="center">
  <img src="assets/logo.svg" width="128" alt="trawl logo: a cast net spinning open, with the brand fish at its centre">
</p>

<h1 align="center">trawl</h1>

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
| M0 | Collect from Greenhouse, Lever, Ashby, Workday, SmartRecruiters, MyCareersFuture; daily Telegram digest | ✅ |
| M1 | Judge each job by its description (model facts, rule verdicts), score fit against the résumé, weekly skill-gap report; evals | ✅ |
| M2 | A résumé tailored to each good fit and drafted form answers, sent with the digest | ✅ |
| M3 | Application filler: pre-fill in Chrome, approve in the terminal, submit, audit log (Greenhouse, Ashby, Lever) | ✅ |
| M4 | Desktop app on the Mac: jobs, applications, progress, funnel; prepare and apply from it ✅; status from Gmail next | ◐ |
| M5 | Multi-tenant, RBAC, PII encryption, tracing, delivery docs | |

## Run it

```bash
docker compose up -d db                  # Postgres (with pgvector)
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env                     # add a Telegram bot token to get the digest on your phone
.venv/bin/trawl collect                  # fetch every board in sources.toml
.venv/bin/trawl process                  # verdict per posting: rules, then DeepSeek (DEEPSEEK_API_KEY)
.venv/bin/trawl tailor 676 --out out/    # on demand: one job's tailored résumé and form answers
.venv/bin/trawl tailor                   # or every new job scoring 60+
.venv/bin/trawl profile init             # a key for the encrypted profile (put it in .env)
.venv/bin/trawl profile edit             # what fills forms: name, contacts, dates, visa (never sent to the model)
.venv/bin/trawl digest --dry-run         # print what the digest would send
.venv/bin/trawl run                      # collect, process, digest: what the server's timer runs
.venv/bin/trawl gaps                     # skills the jobs ask for that the résumé doesn't show
.venv/bin/trawl where                    # where else to apply for MyCareersFuture jobs (citizens and PRs only there)
```

The server collects, judges and scores every day. Tailoring and applying happen on the Mac, for
the jobs you pick, in the desktop app or from the terminal.

### Desktop app

```bash
.venv/bin/pip install -e ".[app]"        # FastAPI, pywebview, Playwright
echo "TRAWL_SSH_HOST=user@host" >> .env  # read the server's database through ssh while the app is open
.venv/bin/trawl app                      # or ./macos/build.sh once, then open trawl from Applications
```

Jobs to do, in progress and done, each with its fit, a table of its requirements against the résumé, the tailored résumé, the drafted form,
screenshots and every step trawl took. **Prepare** tailors the résumé and drafts the form;
**Apply** opens Chrome and fills it, and what `trawl apply` would ask in a terminal (submit? did it go
through?) is asked on the page instead. Also record applications made elsewhere (MyCareersFuture),
interviews, offers and rejections; and the funnel from eligible to offer. The app runs the code in
this checkout, so `git pull` needs only a restart.

### Applying from the terminal

Applying runs on your own machine, in a visible Chrome window, never on the server:

```bash
.venv/bin/pip install -e ".[apply]"      # Playwright; it drives the Chrome you have installed
TRAWL_DEPLOY_HOST=user@host deploy/deploy.sh tunnel   # the server's database on localhost:5433
export TRAWL_DATABASE_URL=postgresql://trawl:trawl@localhost:5433/trawl
.venv/bin/trawl apply 676 --no-submit    # fill the form to look at; nothing is sent
.venv/bin/trawl apply 676                # fill, you finish and check it, type "submit" to send
.venv/bin/trawl apply 676 --log          # every step it took
```

Or entirely in Docker: `docker compose run --rm trawl run`.

Which companies and searches to watch lives in [`sources.toml`](sources.toml). Who the jobs are for
(graduation date, experience, work pass) is personal, so it lives outside the repo in
`~/.trawl/criteria.toml`: copy [`criteria.example.toml`](criteria.example.toml) there and fill it in.
After editing it, `trawl process --redecide` re-applies it without new model calls. The résumé is
read from `~/.trawl/resume.tex` (or `TRAWL_RESUME`), spare bullets for tailoring from
`~/.trawl/bank.toml` (format in [`bank.example.toml`](bank.example.toml)), and tailored résumés
compile with XeLaTeX against the résumé's class and fonts in `~/.trawl/resume-class`.
`deploy/deploy.sh profile` copies all of these from the résumé repo to the server;
`TRAWL_DEPLOY_HOST=local deploy/deploy.sh profile` fills this machine's `~/.trawl`.

## Tests

```bash
.venv/bin/pytest                                        # offline: recorded board responses
TRAWL_TEST_DATABASE_URL=postgresql://trawl:trawl@localhost:5432/trawl .venv/bin/pytest   # + Postgres
.venv/bin/ruff check .
.venv/bin/python evals/run_evals.py                     # live model on hand-checked postings (costs money)
```

## Design

See [`docs/design.md`](docs/design.md).
