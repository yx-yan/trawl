"""What to put in each field of a Greenhouse or Ashby application form, worked out before a browser opens.

The drafted answers (M2b) say, per question, what fills it: an option, a draft, or a profile field
by name. `plan` turns them into steps for the filler (`fill.py`), reading text values from the
encrypted profile only now, in memory. Steps from the profile are marked secret, so nothing that
logs a plan logs a value. Everything trawl can't or mustn't fill (consents, files other than the
résumé, questions that wait for you) is listed as left for the person, who finishes the form in the
browser before approving it.

Greenhouse's form page shows a few fields its API doesn't list (the phone's country, the city
picker, school, degree and discipline, and a second education entry behind "Add another");
`EXTRAS` fills those from the profile when the page has them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .answer import _norm, pick
from .profile import Profile, value

Kind = Literal["text", "choose", "check", "pick", "tick", "file"]


@dataclass
class Step:
    field: str  # the element's id on the page (for "check": the checkbox group's name)
    label: str
    kind: Kind  # type text, choose in a combobox, tick checkboxes (Greenhouse), pick labelled
    # radios or checkboxes (Ashby), tick a lone consent checkbox, attach a file
    value: str | list[str]
    secret: bool = False  # from the profile: never logged or printed
    optional: bool = False  # a field the page may not have (EXTRAS)
    opens: str = ""  # a button that adds the field to the page, clicked when it isn't there yet

    def shown(self) -> str:
        if self.secret:
            return "‹profile›"
        if isinstance(self.value, list):
            return ", ".join(self.value)
        return self.value if len(self.value) <= 60 else self.value[:57] + "..."


@dataclass
class Plan:
    steps: list[Step]
    left: list[str]  # labels of what the person fills in the browser
    consents: list[str] = field(default_factory=list)  # labels of consents given from the profile

    def log(self) -> dict:
        """What the audit log keeps: which fields, never their values."""
        return {"steps": [{"field": s.field, "kind": s.kind, "secret": s.secret} for s in self.steps],
                "left": self.left, "consents": self.consents}


# The API's "location" question is a city picker on the page
DOM_IDS = {"location": "candidate-location"}
COMBOBOXES = {"candidate-location"}
# Fields only the page has: (element id, label, profile field). The second education entry appears
# after "Add another".
EXTRAS = [("country", "Phone country", "country"), ("candidate-location", "Location (City)", "city"),
          ("school--0", "School", "university"), ("degree--0", "Degree", "degree"),
          ("discipline--0", "Discipline", "major"),
          ("school--1", "Earlier school", "previous_university"),
          ("degree--1", "Earlier degree", "previous_degree"),
          ("discipline--1", "Earlier discipline", "previous_major")]
PUBLIC = {"university", "previous_university"}  # shown when planning; the rest print as ‹profile›


def dom_id(question_id: str) -> str:
    """The element for a question: demographic_591 is #591; the rest keep the API's name."""
    if question_id.startswith("demographic_"):
        return question_id.removeprefix("demographic_")
    return DOM_IDS.get(question_id, question_id)


def plan(answers: list[dict], profile: Profile | None, source: str = "greenhouse") -> Plan:
    gh = source == "greenhouse"
    steps: list[Step] = []
    left: list[str] = []
    consents: list[str] = []
    for a in answers:
        a = refill(a, profile)
        if (given := consent(a, profile)) is not a:
            consents.append(a["question"]["label"])
            a = given
        q = a["question"]
        field, label = dom_id(q["id"]) if gh else q["id"], q["label"]
        status, v = a["status"], a["value"]
        if status == "needs_you":
            left.append(f"{label} ({a['note']})" if a.get("note") else label)
        elif status == "drafted":
            steps.append(Step(field, label, "text", v))
        elif status == "filled":
            step = _filled(field, label, a, profile, gh)
            if step is None:
                left.append(f"{label} (not in your profile: {a['source']})")
            else:
                steps.append(step)
    have = {s.field for s in steps}
    for field, label, key in EXTRAS if gh else []:
        v = value(profile, key) if profile else None
        if key in ("degree", "previous_degree") and isinstance(v, str):
            v = degree_level(v)
        if field not in have and isinstance(v, str) and v:
            steps.append(Step(field, label, "choose", [v], secret=key not in PUBLIC, optional=True,
                              opens="Add another" if field == "school--1" else ""))
    return Plan(steps, left, consents)


_MISSING = re.compile(r"(?:not in your profile|no option matches your): (\w+)")


# Kinds of consent, tried in this order: an AI notetaker's notice also mentions data
CONSENTS = [
    ("recording", "agree_recording",
     re.compile(r"\b(record(ing|ed)?|notetak\w*|transcri\w*)\b.*\binterview|"
                r"\binterview\w*\b.*\b(record|notetak|transcri)", re.I | re.S)),
    ("truthful", "agree_truthful",
     re.compile(r"\b(information|statements?|answers?|application)\b.*"
                r"\b(true|accurate|complete|truthful)\b", re.I | re.S)),
    ("privacy", "agree_privacy",
     re.compile(r"privacy (policy|notice)|personal (data|information)|\bgdpr\b|"
                r"(collect|stor|process)\w*\b.*\b(data|responses|information)", re.I | re.S)),
]


def consent_kind(label: str) -> tuple[str, str] | None:
    """Which kind of consent a label asks for, and the profile field that may give it."""
    for kind, key, pattern in CONSENTS:
        if pattern.search(label):
            return kind, key
    return None


_YES = re.compile(r"^(yes|i agree|i consent|i accept|agree|accept)\b", re.I)


def consent(a: dict, profile: Profile | None) -> dict:
    """A consent the profile gives for its kind, answered; any other stays with the person."""
    if a["status"] != "needs_you" or not (a.get("note") or "").startswith("a consent") or profile is None:
        return a
    found = consent_kind(a["question"]["label"])
    if not found or getattr(profile, found[1]) is not True:
        return a
    q, kind = a["question"], found[0]
    if q["type"] in ("select", "multi_select") and any(o.strip() for o in q["options"]):
        yes = [o for o in q["options"] if _YES.match(o.strip())]
        if len(yes) != 1:
            return a
        return a | {"status": "filled", "value": yes, "source": f"consent:{kind}", "note": ""}
    return a | {"status": "filled", "value": True, "source": f"consent:{kind}", "note": ""}


def refill(a: dict, profile: Profile | None) -> dict:
    """A question that waited for a profile field when it was drafted, answered from the profile as
    it is now: filling in the profile helps the forms already drafted, without a model call."""
    m = _MISSING.fullmatch(a.get("note") or "") if a["status"] == "needs_you" else None
    v = value(profile, m.group(1)) if m and profile else None
    if v is None or v == "":
        return a
    q, key = a["question"], m.group(1)
    if q["type"] in ("select", "multi_select"):
        chosen = pick(q["options"], v)
        return a | {"status": "filled", "value": chosen, "source": key, "note": ""} if chosen else a
    if q["type"] == "boolean":
        return a | {"status": "filled", "value": bool(v), "source": key, "note": ""}
    if isinstance(v, bool):
        return a
    return a | {"status": "filled", "value": None, "source": key, "note": ""}  # text: read when filling


def _filled(field: str, label: str, a: dict, profile: Profile | None, gh: bool) -> Step | None:
    v = a["value"]
    if a["source"] == "resume":
        return Step(field, label, "file", "resume")
    if isinstance(v, list):
        # Greenhouse shows a multi-select as checkboxes named question_x[] and the rest as
        # comboboxes; Ashby shows every choice as labelled radios or checkboxes
        if not gh:
            return Step(field, label, "pick", v)
        return Step(field, label, "check" if field.endswith("[]") else "choose", v)
    if a["source"].startswith("consent:") and v is True:  # a lone checkbox the profile agrees to
        return Step(field, label, "tick", "")
    if isinstance(v, bool):  # Ashby's yes/no questions
        return None if gh else Step(field, label, "pick", ["Yes" if v else "No"])
    if v is None:  # a text field filled from the profile, now
        text = value(profile, a["source"]) if profile else None
        if text is None or text == "" or isinstance(text, bool):
            return None
        return Step(field, label, "choose" if field in COMBOBOXES else "text", [text] if field in COMBOBOXES
                    else text, secret=True)
    return None


_LEVELS = [  # Greenhouse's degree picker lists levels, not degree names
    (r"\bm\.?b\.?a\b|business administration", "Master of Business Administration (M.B.A.)"),
    (r"\bph\.?\s?d\b|doctor of philosophy|doctorate", "Doctor of Philosophy (Ph.D.)"),
    (r"\bmaster|\bm\.?\s?(sc|eng|s|a|phil|tech)\b", "Master's Degree"),
    (r"\bbachelor|\bb\.?\s?(sc|eng|s|a|comp|tech|e)\b", "Bachelor's Degree"),
    (r"\bassociate", "Associate's Degree"),
]


def degree_level(degree: str) -> str:
    """'Master of Science (Signal Processing)' -> "Master's Degree"; unknown names stay as they are."""
    for pattern, level in _LEVELS:
        if re.search(pattern, degree, re.I):
            return level
    return degree


def match_option(options: list[str], want: str) -> int | None:
    """The option to pick for a wanted value: the exact one, else the only one containing it.
    Never the first of several ("Nanyang" matches a polytechnic before the university)."""
    n = _norm(want)
    norms = [_norm(o) for o in options]
    exact = [i for i, o in enumerate(norms) if o == n]
    if exact:
        return exact[0]
    near = [i for i, o in enumerate(norms) if n and n in o]
    return near[0] if len(near) == 1 else None
