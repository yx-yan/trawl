"""Can the candidate apply, and should they? Plain rules over facts, so every verdict has reasons.

Two kinds of facts feed the rules:
- board facts (Posting.facts), structured fields some boards provide: `employment_types`,
  `position_levels`, `salary_min`, `salary_max` (monthly SGD), `min_years`, `ssic`;
- job facts (extract.JobFacts), what the model read in the description.

`prefilter` uses only the title and board facts, so most postings are rejected before any model
call; `decide` makes the final verdict once the job facts exist.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .extract import JobFacts

SENIOR_TITLE = re.compile(
    r"\b(senior|sr|staff|lead|principal|manager|head|director|vp|vice president|chief|"
    r"recruit\w*|account executive)\b", re.I)
# A title with none of these is not a technical role worth a model call
TECH_TITLE = re.compile(
    r"engineer|developer|software|programmer|scientist|architect|technical|technolog|\bai\b|\bml\b|"
    r"machine learning|\bdata\b|solution|deploy|platform|cloud|devops|\bsre\b|\binterns?(hip)?\b|graduate|"
    r"trainee|associate|\bit\b|analyst|consultant|forward", re.I)


@dataclass
class Criteria:
    graduation: str = "2027-06"  # YYYY-MM the candidate graduates
    max_years: int = 2
    needs_sponsorship: bool = True
    ep_min_salary: int = 6000  # SGD/month; MOM, new applications from 1 Jan 2027
    s_pass_min_salary: int = 3600
    allowed_languages: list[str] = field(default_factory=lambda: ["english", "mandarin", "chinese"])
    excluded_employment: list[str] = field(default_factory=lambda: [
        "contract", "temporary", "freelance", "part time", "flexi work"])
    excluded_levels: list[str] = field(default_factory=lambda: [
        "manager", "middle management", "senior management"])
    excluded_ssic_prefixes: list[str] = field(default_factory=lambda: ["78"])
    exclude_agencies: bool = True

    @classmethod
    def load(cls, path: Path) -> Criteria:
        return cls(**tomllib.loads(path.read_text())) if path.is_file() else cls()


@dataclass
class Verdict:
    eligible: bool
    reasons: list[str] = field(default_factory=list)  # why not, when not eligible
    flags: list[str] = field(default_factory=list)  # worth knowing, when eligible


def prefilter(title: str, facts: dict, c: Criteria) -> list[str]:
    """Reasons to reject from the title and board facts alone; empty means ask the model."""
    reasons = []
    if m := SENIOR_TITLE.search(title):
        reasons.append(f"senior title ({m.group(0)})")
    if not TECH_TITLE.search(title):
        reasons.append("not a technical title")
    types = [t.lower() for t in facts.get("employment_types") or []]
    if types and all(t in c.excluded_employment for t in types):
        reasons.append(f"employment: {', '.join(types)}")
    levels = [lv.lower() for lv in facts.get("position_levels") or []]
    if levels and all(lv in c.excluded_levels for lv in levels):
        reasons.append(f"level: {', '.join(levels)}")
    if (years := facts.get("min_years")) is not None and years > c.max_years:
        reasons.append(f"needs {years}+ years")
    ssic = str(facts.get("ssic") or "")
    if c.exclude_agencies and any(ssic.startswith(p) for p in c.excluded_ssic_prefixes):
        reasons.append("posted by an agency")
    is_intern = "internship/attachment" in types
    salary_max = facts.get("salary_max")
    if c.needs_sponsorship and not is_intern and salary_max and salary_max < c.s_pass_min_salary:
        reasons.append(f"pay up to S${salary_max} is below the S Pass minimum")
    return reasons


def decide(title: str, facts: dict, job: JobFacts, c: Criteria) -> Verdict:
    reasons = prefilter(title, facts, c)
    if job.track == "other":
        reasons.append(f"not FDE or a path to it: {job.reason}")
    if job.seniority in ("mid", "senior") and job.min_years is None:
        reasons.append(f"{job.seniority}-level")
    if job.min_years is not None and job.min_years > c.max_years:
        reasons.append(f"needs {job.min_years}+ years")
    if job.employment in ("contract", "part_time"):
        reasons.append(f"employment: {job.employment.replace('_', ' ')}")
    if c.exclude_agencies and job.agency:
        reasons.append("posted by an agency")
    if c.needs_sponsorship and job.work_authorization in ("citizens_pr_only", "no_sponsorship",
                                                          "clearance_required"):
        reasons.append({"citizens_pr_only": "citizens / PR only", "no_sponsorship": "no visa sponsorship",
                        "clearance_required": "security clearance"}[job.work_authorization])
    if not job.based_in_singapore:
        reasons.append("based outside Singapore")
    if job.graduation_from and c.graduation < job.graduation_from:
        reasons.append(f"for graduates from {job.graduation_from}")
    if job.graduation_to and c.graduation > job.graduation_to:
        reasons.append(f"for graduates up to {job.graduation_to}")
    if job.requires_phd:
        reasons.append("PhD required")
    if other := [lang for lang in job.required_languages if lang.lower() not in c.allowed_languages]:
        reasons.append(f"requires {', '.join(other)}")
    if not job.writes_code:
        reasons.append("doesn't write code")

    flags = []
    if job.seniority == "intern" or job.employment == "internship":
        flags.append("internship")
    salary_max = facts.get("salary_max")
    if (c.needs_sponsorship and "internship" not in flags and salary_max
            and c.s_pass_min_salary <= salary_max < c.ep_min_salary):
        flags.append("S Pass range pay")
    if job.work_authorization == "open":
        flags.append("sponsorship mentioned")
    if job.work_authorization == "prefers_local":
        flags.append("prefers local candidates")
    if job.intake:
        flags.append(job.intake if len(job.intake) <= 60 else job.intake[:59] + "…")
    reasons = list(dict.fromkeys(reasons))  # board and model facts can give the same reason
    return Verdict(eligible=not reasons, reasons=reasons, flags=flags)
