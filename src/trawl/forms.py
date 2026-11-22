"""The questions on a job's application form, in one shape whatever the board.

- Greenhouse publishes them: `boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>?questions=true`.
- Ashby has no documented endpoint for them; its own job page reads them from a public GraphQL
  endpoint (`jobs.ashbyhq.com/api/non-user-graphql`), which trawl reads the same way. It may change
  without notice, so a failure means "no questions", never a failed run.
- Lever and MyCareersFuture: not yet (Lever's questions are only in the apply page's HTML;
  MyCareersFuture applies through the candidate's own signed-in profile).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import httpx

from .connectors.parsing import html_to_text


@dataclass
class Question:
    id: str  # the form's own field name, used to fill it in M3
    label: str
    required: bool
    type: str  # text, long_text, file, select, multi_select, number, boolean, date
    options: list[str] = field(default_factory=list)
    demographic: bool = False  # voluntary EEO questions: answered only from the profile, or declined
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_GREENHOUSE_TYPES = {"input_text": "text", "textarea": "long_text", "input_file": "file",
                     "multi_value_single_select": "select", "multi_value_multi_select": "multi_select"}


def greenhouse(http: httpx.Client, board: str, job_id: str) -> list[Question]:
    resp = http.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}",
                    params={"questions": "true"})
    resp.raise_for_status()
    data = resp.json()
    out = []
    for q in (data.get("questions") or []) + (data.get("location_questions") or []):
        fields = [f for f in q.get("fields") or [] if f.get("type") in _GREENHOUSE_TYPES]
        if not fields:  # hidden inputs such as latitude
            continue
        # "Resume/CV" offers a file or a textarea: the first field is the one to use
        f = fields[0]
        out.append(Question(f["name"], " ".join(q["label"].split()), bool(q.get("required")),
                            _GREENHOUSE_TYPES[f["type"]], [str(v["label"]) for v in f.get("values") or []],
                            description=html_to_text(q.get("description") or "")[:500]))
    demo = data.get("demographic_questions") or {}
    for q in demo.get("questions") or []:
        kind = "multi_select" if "multi" in (q.get("type") or "") else "select"
        out.append(Question(f"demographic_{q.get('id', len(out))}", q["label"], bool(q.get("required")), kind,
                            [o["label"] for o in q.get("answer_options") or []], demographic=True))
    return out


_ASHBY_TYPES = {"String": "text", "Email": "text", "Phone": "text", "Location": "text",
                "LongText": "long_text", "File": "file", "ValueSelect": "select",
                "MultiValueSelect": "multi_select",
                "Number": "number", "Boolean": "boolean", "Date": "date"}
_ASHBY_QUERY = (
    "query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) { "
    "jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, "
    "jobPostingId: $jobPostingId) "
    "{ id applicationForm { sections { fieldEntries { ... on FormFieldEntry { isRequired field } } } } } }"
)


def ashby(http: httpx.Client, board: str, posting_id: str) -> list[Question]:
    resp = http.post("https://jobs.ashbyhq.com/api/non-user-graphql",
                     json={"operationName": "ApiJobPosting", "query": _ASHBY_QUERY,
                           "variables": {"organizationHostedJobsPageName": board,
                                         "jobPostingId": posting_id}})
    resp.raise_for_status()
    posting = (resp.json().get("data") or {}).get("jobPosting")
    if not posting:
        raise ValueError(f"ashby: no form for {board}/{posting_id}")
    out = []
    for section in posting["applicationForm"]["sections"]:
        for entry in section["fieldEntries"]:
            f = entry.get("field") or {}
            kind = _ASHBY_TYPES.get(f.get("type"))
            if kind is None or f.get("isDeactivated"):
                continue
            options = [v["label"] for v in f.get("selectableValues") or [] if not v.get("isArchived")]
            if kind == "multi_select" and options in ([""], []):  # one unlabelled box: a checkbox
                kind, options = "boolean", []
            title = " ".join(f.get("title", "").split())
            out.append(Question(f["path"], title, bool(entry.get("isRequired")), kind, options))
    return out


def board_from_url(url: str) -> str | None:
    """Ashby's board name from a job URL: jobs.ashbyhq.com/<board>/<id>."""
    m = re.match(r"https?://jobs\.ashbyhq\.com/([^/]+)/", url)
    return m.group(1) if m else None


def fetch(http: httpx.Client, source: str, external_id: str, facts: dict, url: str) -> list[Question] | None:
    """The form's questions, or None when trawl can't read this board's forms."""
    if source == "greenhouse" and facts.get("board"):
        return greenhouse(http, facts["board"], external_id)
    if source == "ashby" and (board := facts.get("board") or board_from_url(url)):
        return ashby(http, board, external_id)
    return None
