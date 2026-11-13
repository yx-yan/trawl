# Design notes

## 1. Built like a client delivery

trawl does a real job (my own FDE search) and is also a portfolio piece for FDE roles. So
each part is built the way I'd build it for a customer: integrations through documented
public APIs, a database that keeps everything it saw, tests against recorded responses, a
Docker deployment, and a person approving anything that acts in the world.

## 2. Public board APIs only

Greenhouse, Lever and Ashby publish every company's open jobs as JSON, and MyCareersFuture
(Singapore's national job portal) has a public search API. That covers most AI startups and
the Singapore market without scraping. LinkedIn is never scraped: its terms forbid it and
accounts get banned. LinkedIn jobs will come in through its alert emails or a pasted link.

Each connector is one function, `fetch(http, key) -> list[Posting]`, so adding a board means
adding one module. A board that fails is reported and skipped; one company changing its
board name must not stop the daily run.

## 3. Keep every posting in a wanted location, not only title matches

The same work hides under many titles ([roles.md](roles.md)), so every posting in Singapore
is stored and judged by its description, not by whether its title looks right.

## 4. Same job, several boards

A company can post the same role on its own board and on MyCareersFuture, where the name is
the registered company ("ANTHROPIC SINGAPORE PTE. LTD."). A fingerprint of normalized company
and title links the two; the digest sends one copy (preferring the company's own board) and
never sends a fingerprint again once any copy was sent.

## 5. Nothing is submitted without approval

Planned for M3, decided now: trawl may pre-fill an application, but submitting waits for a
person to approve that exact application, and every step is written to an audit log.
Sensitive answers (phone number, visa status) come from an encrypted store and are never put
in a model prompt.

## 6. The model reports facts; rules decide

Whether a job suits me depends on things that change (my graduation date, how much experience
counts as too much, whether I need a work pass), while what a posting says doesn't. So the model
never answers "should I apply?". It fills in `JobFacts` (`src/trawl/extract.py`): track, seniority,
minimum years, employment type, agency, work-authorization terms, graduation window, location,
PhD, required languages, whether it writes code. Plain rules in `src/trawl/decide.py` turn those
facts and [`criteria.toml`](../criteria.toml) into a verdict with reasons.

This buys three things. Every rejection has a reason I can read ("needs 5+ years", "citizens /
PR only"), and the digest tallies them. Changing my criteria is `trawl process --redecide`, with no
model calls. And the facts are about the job, not me, so the same extraction would serve a second
candidate unchanged.

Two lessons from real postings shaped the schema:

- **Preference is not a requirement.** "Particularly interested in applicants with a current right
  to work in Singapore" first came back as citizens-only and dropped Palantir's new-grad role.
  `prefers_local` is now its own value: a flag in the digest, never a reason to reject.
- **Graduation windows are dates, not prose.** "Summer 2027 intern, graduating Dec 2027 - Jun 2028"
  was first kept as text, which no rule could check. The model now returns `graduation_from` /
  `graduation_to` as YYYY-MM, with a stated mapping for vague dates ("late 2027" -> 2027-09), and
  a validator turns the string "null" the model sometimes writes into a real null.

## 7. Cheapest checks first

Most postings are rejected without a model call: a senior title, a contract, an agency (SSIC
78xxx on MyCareersFuture), pay below the S Pass minimum, too many required years. MyCareersFuture's
search results have no description, so its detail is fetched only for postings that survive the
first rules, and the rules run again with the agency code and years it adds. On the first real
run, 696 of about 900 postings stopped at the rules; the model read 203.

Pay matters only as a visa fact: a full-time job paying under the S Pass minimum can't sponsor a
pass. Internships are exempt, since students intern on their Student's Pass.

DeepSeek's thinking mode refuses a forced `tool_choice`, so extraction runs with thinking off; it
is a lookup, not a reasoning task. A posting whose call fails keeps no verdict and is retried on
the next run.

## 8. Evals: hand-checked facts from real postings

`evals/cases.json` holds snapshots of real postings (they change and close), each with the facts I
checked by reading it and whether it should get through. `python evals/run_evals.py` runs them
against the live model; it caught the two lessons in section 6 and a "late 2027" that came back
once as 2027-01. Offline tests use a fake client and cover the rules exhaustively.
