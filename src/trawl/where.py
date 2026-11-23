"""Where to apply for a job trawl found somewhere it can't apply: MyCareersFuture takes applications
only from Singapore citizens and permanent residents, but its postings are often the ones employers
must advertise before hiring on an Employment Pass, so the same job can be applied to elsewhere.

`leads` looks, cheapest first, for:
1. the same job (same fingerprint) on another board trawl already collects;
2. the company's own board on Greenhouse, Lever or Ashby, guessed from its name and website; with
   the job on it, that's a link trawl can fill. A board guessed from a single word of the name
   counts only if the job is on it: "dcs" may be someone else's board;
3. the company's website (MyCareersFuture's company record) and its careers page, if it has one at
   /careers or /jobs;
4. a LinkedIn and a Google search for the job, as links for the person to open: trawl never scrapes
   them.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from urllib.parse import quote_plus, urlsplit

import httpx

from .connectors import CONNECTORS, mcf
from .models import Posting, normalize, normalize_company
from .store import Store

BOARDS = ("greenhouse", "lever", "ashby")  # the boards trawl can fill
BOARD_PAGES = {"greenhouse": "https://job-boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}",
               "ashby": "https://jobs.ashbyhq.com/{}"}
PROBE_TIMEOUT = 8


@dataclass
class Lead:
    kind: str  # posting, board, careers, website, search
    label: str
    url: str
    fillable: bool = False  # a posting on a board trawl can fill
    board: str = ""  # with `slug`: a board trawl could collect (sources.toml)
    slug: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def slugs(company: str, website: str | None = None) -> tuple[list[str], list[str]]:
    """Board names to try: (from the whole name or the website's domain, from its first word only)."""
    words = normalize_company(company).split()
    whole = {"".join(words), "-".join(words)} if words else set()
    if website and (host := urlsplit(website if "//" in website else f"//{website}").hostname):
        stem = host.removeprefix("www.").split(".")[0]
        whole |= {stem, stem.replace("-", "")}
    first = {words[0]} - whole if len(words) > 1 and len(words[0]) > 2 else set()
    return sorted(s for s in whole if len(s) > 2), sorted(first)


def same_title(a: str, b: str) -> bool:
    na, nb = normalize(a), normalize(b)
    return bool(na and nb) and (na == nb or na in nb or nb in na)


def in_singapore(p: Posting) -> bool:
    return "singapore" in p.location.lower() or not p.location


def board_leads(http: httpx.Client, company: str, title: str, website: str | None) -> list[Lead]:
    whole, first = slugs(company, website)
    tries = [(board, slug, slug in whole) for board in BOARDS for slug in whole + first]

    def probe(t: tuple[str, str, bool]) -> list[Lead]:
        board, slug, trusted = t
        try:
            postings = CONNECTORS[board](http, slug)
        except (httpx.HTTPError, KeyError, ValueError):
            return []
        if not postings:
            return []
        found = [p for p in postings if same_title(p.title, title) and in_singapore(p)]
        if found:
            return [Lead("posting", f"{p.title} on {board.capitalize()}", p.url, True, board, slug)
                    for p in found[:2]]
        page = BOARD_PAGES[board].format(slug)
        return [Lead("board", f"Jobs on {board.capitalize()}", page, False, board, slug)] if trusted else []

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [lead for found in pool.map(probe, tries) for lead in found]


def company_site(http: httpx.Client, external_id: str) -> str | None:
    """The company's website from MyCareersFuture: the job names the company, its record the site."""
    try:
        job = http.get(f"{mcf.API}/jobs/{external_id}").raise_for_status().json()
        company = job.get("hiringCompany") or job.get("postedCompany") or {}
        if not company.get("uen"):
            return None
        record = http.get(f"{mcf.API}/companies/{company['uen']}").raise_for_status().json()
    except (httpx.HTTPError, ValueError):
        return None
    url = (record.get("companyUrl") or "").strip()
    return url if url.startswith("http") else (f"https://{url}" if url else None)


def careers_page(http: httpx.Client, website: str) -> str | None:
    for path in ("careers", "jobs", "career"):
        url = f"{website.rstrip('/')}/{path}"
        try:
            resp = http.get(url, follow_redirects=True)
        except httpx.HTTPError:
            continue
        if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
            return str(resp.url)
    return None


def search_leads(company: str, title: str) -> list[Lead]:
    name = normalize_company(company) or company
    linkedin = f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(f'{title} {name}')}"
    return [
        Lead("search", "Search LinkedIn", f"{linkedin}&location=Singapore"),
        Lead("search", "Search Google",
             f"https://www.google.com/search?q={quote_plus(f'{name} {title} careers apply')}"),
    ]


def leads(http: httpx.Client, *, source: str, external_id: str, company: str, title: str,
          siblings: list[dict]) -> list[Lead]:
    """Every place found to apply for this job, the most direct first."""
    out = [Lead("posting", f"#{s['id']} on {s['source'].capitalize()}", s["url"], s["source"] in BOARDS)
           for s in siblings if s["source"] != source]  # a second copy on the same board helps nobody
    website = company_site(http, external_id) if source == "mcf" else None
    out += board_leads(http, company, title, website)
    if website:
        if careers := careers_page(http, website):
            out.append(Lead("careers", "Careers page", careers))
        out.append(Lead("website", "Company website", website))
    seen: set[str] = set()
    unique = [lead for lead in out if not (lead.url in seen or seen.add(lead.url))]
    return unique + search_leads(company, title)


def client() -> httpx.Client:
    from .cli import USER_AGENT

    return httpx.Client(timeout=PROBE_TIMEOUT, headers={"User-Agent": USER_AGENT})


def find(store: Store, http: httpx.Client, posting_id: int) -> list[dict]:
    """Look for a job's leads now and keep them with the posting."""
    p = store.posting(posting_id)
    if p is None:
        raise ValueError(f"#{posting_id}: no such posting")
    found = [lead.to_dict() for lead in leads(http, source=p.source, external_id=p.external_id,
                                              company=p.company, title=p.title,
                                              siblings=store.siblings(posting_id))]
    store.save_leads(posting_id, found)
    return found
