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

The same work hides under many titles ([roles.md](roles.md)). Titles decide what the M0
digest shows, but every posting in a wanted location is stored, so M1's classifier can read
the descriptions of jobs whose titles didn't match.

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
