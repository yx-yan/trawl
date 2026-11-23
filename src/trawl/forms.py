"""The questions on a job's application form, in one shape whatever the board.

- Greenhouse publishes them: `boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>?questions=true`.
- Ashby has no documented endpoint for them; its own job page reads them from a public GraphQL
  endpoint (`jobs.ashbyhq.com/api/non-user-graphql`), which trawl reads the same way. It may change
  without notice, so a failure means "no questions", never a failed run.
- Lever has no API for them, but its apply page (`jobs.lever.co/<board>/<id>/apply`) is plain HTML:
  the standard fields (résumé, name, email, location, links) are labelled inputs, and each block of
  custom questions carries its own definition in a hidden `cards[<id>][baseTemplate]` input, as
  JSON with each field's type, text, options and whether it's required.
- MyCareersFuture: not at all (it applies through the candidate's own signed-in profile).
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser

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


_LEVER_TYPES = {"text": "text", "textarea": "long_text", "multiple-choice": "select",
                "multiple-select": "multi_select", "dropdown": "select"}
_LEVER_SKIP = {"comments"}  # "Additional information": a free-for-all nobody needs drafted


class _LeverPage(HTMLParser):
    """The apply page's questions: labelled standard fields, the cards' JSON templates, EEO selects."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.questions: list[Question] = []
        self.cards: list[tuple[str, dict]] = []
        self._label: list[str] | None = None  # text of the current .application-label, while inside it
        self._depth = 0
        self._last_label = ""
        self._select: dict | None = None  # an EEO select being read
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = {k: v or "" for k, v in attrs}
        classes = a.get("class", "").split()
        if self._label is not None:
            self._depth += tag == "div"
        elif tag == "div" and "application-label" in classes:
            self._label, self._depth = [], 1
        name, kind = a.get("name", ""), a.get("type", "text")
        if tag == "input" and name.endswith("[baseTemplate]"):
            with contextlib.suppress(ValueError):  # a template that isn't JSON: its questions stay unread
                self.cards.append((name.removesuffix("[baseTemplate]"), json.loads(a.get("value") or "{}")))
            return
        if name.startswith("eeo[") and tag == "select":
            self._select = {"name": name, "options": []}
            return
        if tag == "option" and self._select is not None:
            self._select["value"] = a.get("value", "")
            return
        if (tag not in ("input", "textarea") or not name or name.startswith("cards[") or name in _LEVER_SKIP
                or kind in ("hidden", "submit", "button") or name in self._seen):
            return
        self._seen.add(name)
        label = re.sub(r"\s*✱\s*$", "", self._last_label).strip() or name
        required = "required" in a or "✱" in self._last_label
        qtype = "file" if kind == "file" else "long_text" if tag == "textarea" else "text"
        self.questions.append(Question(name, label, required, qtype))

    def handle_endtag(self, tag: str) -> None:
        if self._label is not None and tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self._last_label = " ".join("".join(self._label).split())
                self._label = None
        if tag == "select" and self._select is not None:
            s = self._select
            labels = [o for o in s["options"] if o]
            title = s["name"][4:-1].replace("_", " ").capitalize()
            self.questions.append(Question(s["name"], title, False, "select", labels, demographic=True))
            self._select = None

    def handle_data(self, data: str) -> None:
        if self._label is not None:
            self._label.append(data)
        if self._select is not None and "value" in self._select:
            text = data.strip()
            if text and self._select.pop("value", None):
                self._select["options"].append(text)


def lever(http: httpx.Client, url: str) -> list[Question]:
    resp = http.get(url.rstrip("/").removesuffix("/apply") + "/apply")
    resp.raise_for_status()
    page = _LeverPage()
    page.feed(resp.text)
    out = list(page.questions)
    for card, template in page.cards:
        for i, f in enumerate(template.get("fields") or []):
            kind = _LEVER_TYPES.get(f.get("type"))
            if kind is None:
                continue
            options = [str(o.get("text", "")) for o in f.get("options") or [] if o.get("text")]
            out.append(Question(f"{card}[field{i}]", " ".join((f.get("text") or "").split()),
                                bool(f.get("required")), kind, options,
                                description=html_to_text(f.get("description") or "")[:500]))
    if not out:
        raise ValueError(f"lever: no form at {url}")
    return out


def board_from_url(url: str) -> str | None:
    """The board's name from a job URL: jobs.ashbyhq.com/<board>/<id> or jobs.lever.co/<board>/<id>."""
    m = re.match(r"https?://jobs\.(?:ashbyhq\.com|lever\.co)/([^/]+)/", url)
    return m.group(1) if m else None


def fetch(http: httpx.Client, source: str, external_id: str, facts: dict, url: str) -> list[Question] | None:
    """The form's questions, or None when trawl can't read this board's forms."""
    if source == "greenhouse" and facts.get("board"):
        return greenhouse(http, facts["board"], external_id)
    if source == "ashby" and (board := facts.get("board") or board_from_url(url)):
        return ashby(http, board, external_id)
    if source == "lever" and url:
        return lever(http, url)
    return None
