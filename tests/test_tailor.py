from __future__ import annotations

import re

import httpx
from conftest import FakeClient, job_facts

from trawl import digest
from trawl.decide import Criteria
from trawl.extract import Usage
from trawl.models import Posting
from trawl.process import process, tailor_new
from trawl.store import Application
from trawl.tailor import (
    Tailoring,
    apply,
    edits,
    finish,
    identity,
    invented,
    parse,
    plain,
    prompt_view,
    render,
    source_hash,
    to_latex,
)

TEX = r"""% !TEX TS-program = xelatex
\documentclass{resume-en}
\begin{document}
\name{Yuxuan \surname{Yan}}
\contact{Mobile Phone: (+65) 0000 1111 / Email: \email{me@example.com}}
\links{%
  \href{https://github.com/x}{github.com/x}%
}

% ─── Professional Summary ───
\section{Professional Summary}
NTU M.Sc.\ student, focused on \textbf{LLM agents}. Background in \textbf{PyTorch}.

% TODO(确认): private note
\section{Education}
\entry{Nanyang Technological University}{Aug 2026 -- Jul 2027}
\role{\textbf{Master of Science}}

\section{Projects}
\entry{Solfish: Personal AI Agent}{Oct 2026 -- Present}
\role{Personal Project \textbar\ Python, DeepSeek API, SQLite}
\begin{itemize}
  \item Wrote the agent loop by hand, handling every
        model stop reason.
  \item Wrote \textbf{17 offline tests} run in GitHub Actions CI.
  \item Logged token counts and cost to a 50\% smaller JSONL trace.
\end{itemize}

\entry{Final Year Project: CT Reconstruction}{Jan 2026 -- May 2026}
\role{NTU \textbar\ Python, PyTorch}
\begin{itemize}
  \item Built a dual-domain \textbf{PyTorch} network from 60 X-ray views.
\end{itemize}

\section{Skills}
\begin{itemize}
  \item \textbf{Programming:} Python, Shell
  \item \textbf{ML:} PyTorch, NumPy
\end{itemize}

\end{document}
"""

BANK = [("Solfish", r"Added \textbf{human approval} before every write tool.")]


def choices(**overrides: object) -> Tailoring:
    base = {
        "summary": "NTU M.Sc. student, focused on **LLM agents**. Background in **PyTorch**.",
        "entries": [
            {
                "id": "e2",
                "bullets": [{"id": "e2.b1", "text": "Built a **PyTorch** network from 60 X-ray views."}],
            },
            {
                "id": "e1",
                "bullets": [
                    {"id": "e1.b2", "text": "Wrote **17 offline tests** run in GitHub Actions CI."},
                    {"id": "e1.x1", "text": "Added **human approval** before every write tool."},
                ],
            },
        ],
        "skills": ["s2"],
        "changes": ["led with the CT project"],
    }
    return Tailoring.model_validate(base | overrides)


def test_parse_and_render_give_back_the_master_without_comment_lines() -> None:
    resume = parse(TEX, BANK)
    assert [(s.name, s.kind) for s in resume.sections] == [
        ("Professional Summary", "summary"),
        ("Education", "fixed"),
        ("Projects", "entries"),
        ("Skills", "skills"),
    ]
    assert render(resume, identity(resume)) == re.sub(r"(?m)^[ \t]*%.*\n", "", TEX)


def test_the_model_sees_ids_and_no_contact_details() -> None:
    view = prompt_view(parse(TEX, BANK))
    assert "[e1] Solfish: Personal AI Agent (Oct 2026 -- Present) · Personal Project | Python" in view
    assert "  - [e1.b2] Wrote **17 offline tests** run in GitHub Actions CI." in view
    assert "  - [e1.x1] (spare) Added **human approval** before every write tool." in view
    assert "- [s2] **ML:** PyTorch, NumPy" in view
    assert "0000" not in view and "example.com" not in view and "Yan" not in view and "TODO" not in view


def test_invented_finds_new_numbers_and_names_only() -> None:
    source = "Wrote **17 offline tests** run in GitHub Actions CI, with PyTorch."
    assert invented("Wrote 17 tests with **pytorch** in GitHub Actions CI.", source) == []
    assert invented("Wrote 20 tests, deployed on Kubernetes and AWS.", source) == ["20", "Kubernetes", "AWS"]
    # numbers must come from the bullet itself, names may come from the wider scope
    assert invented("Ran 60 views in PyTorch.", source + " 60 views", numbers_from=source) == ["60"]
    # a capital that starts a sentence is not a name; "Go" is not in "algorithm"
    assert invented("Built it. Then shipped it.", "built and shipped") == []
    assert invented("Wrote algorithms in Go.", "Wrote algorithms.") == ["Go"]


def test_apply_reorders_rewords_and_puts_back_made_up_facts() -> None:
    resume = parse(TEX, BANK)
    t = choices(
        entries=[
            {
                "id": "e1",
                "bullets": [
                    {"id": "e1.b2", "text": "Wrote **40 offline tests** on Kubernetes."},  # invented
                    {"id": "e1.b1", "text": "Hand-wrote the agent loop, handling every model stop reason."},
                    {"id": "nope", "text": "made up bullet"},
                    {"id": "e1.x1", "text": "Added **human approval** before every write tool."},
                ],
            }
        ]
    )
    plan = apply(resume, t)
    assert [b for b, _ in plan.bullets["e1"]] == ["e1.b2", "e1.b1", "e1.x1"]
    assert plan.bullets["e1"][0][1] == r"Wrote \textbf{17 offline tests} run in GitHub Actions CI."
    assert plan.bullets["e1"][1][1] == "Hand-wrote the agent loop, handling every model stop reason."
    assert plan.notes == ["e1.b2 kept as written: 40, Kubernetes not in the source"]
    assert plan.order == {2: ["e1", "e2"]}  # entries the model left out keep their place after
    assert plan.bullets["e2"] == [
        ("e2.b1", r"Built a dual-domain \textbf{PyTorch} network from 60 X-ray views.")
    ]
    assert plan.skills == ["s2", "s1"]  # rows the model left out go last


def test_apply_keeps_the_summary_when_it_invents() -> None:
    resume = parse(TEX)
    plan = apply(resume, choices(summary="Senior engineer with 5 years on **AWS**."))
    assert plan.summary.startswith(r"NTU M.Sc.\ student")
    assert "5, AWS" in plan.notes[0]


def test_render_follows_the_plan() -> None:
    resume = parse(TEX, BANK)
    tex = render(resume, apply(resume, choices()))
    assert tex.index("Final Year Project") < tex.index("Solfish")
    assert r"\item Added \textbf{human approval} before every write tool." in tex
    assert "agent loop" not in tex  # dropped by the model
    assert tex.index(r"\textbf{ML:}") < tex.index(r"\textbf{Programming:}")
    assert tex.startswith(r"\documentclass{resume-en}") and "0000 1111" in tex  # header kept as is


def test_to_latex_escapes_and_bolds() -> None:
    assert (
        to_latex("Cut cost 50% & time for **R&D_team**") == r"Cut cost 50\% \& time for \textbf{R\&D\_team}"
    )
    assert to_latex("odd ** count") == "odd  count"
    assert plain(to_latex("a **b** c_d")) == "a **b** c_d"


def fake_compiler(max_bullets: int):
    """Pretends the page overflows while there are more than `max_bullets` bullets."""
    calls: list[str] = []

    def compile_tex(tex: str) -> tuple[bytes, int]:
        calls.append(tex)
        body = tex.split(r"\section{Skills}")[0]
        return b"%PDF", 1 if body.count(r"\item") <= max_bullets else 2

    compile_tex.calls = calls
    return compile_tex


def test_finish_cuts_the_least_relevant_bullets_until_one_page() -> None:
    resume = parse(TEX, BANK)
    t = choices(
        entries=[
            {
                "id": "e1",
                "bullets": [
                    {"id": i, "text": b.text}
                    for i, b in (
                        ("e1.b1", resume.entry("e1").bullets[0]),
                        ("e1.b2", resume.entry("e1").bullets[1]),
                        ("e1.b3", resume.entry("e1").bullets[2]),
                    )
                ],
            }
        ]
    )
    r = finish(resume, fake_compiler(3), t, Usage(1, 1, 0.1))
    assert r.notes == ["e1.b3 cut to fit one page"]
    assert "JSONL" not in r.tex and "17 offline tests" in r.tex
    assert r.record()["tailoring"]["changes"] == ["led with the CT project"]


def test_source_hash_changes_with_the_bank() -> None:
    assert source_hash(TEX, []) != source_hash(TEX, BANK)


def test_caption_and_send_document() -> None:
    app = Application(
        7,
        "Acme",
        "FDE",
        "https://x",
        {
            "tailoring": {"changes": ["led with evals"]},
            "notes": ["e1.b2 kept as written: AWS not in the source"],
        },
        "",
        b"%PDF",
    )
    assert (
        digest.caption(app)
        == "#7 Acme: FDE\n• led with evals\n⚠ e1.b2 kept as written: AWS not in the source"
    )
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    digest.send_document(
        "r.pdf", b"%PDF", "cap", "TOKEN", "42", httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert sent[0].url.path == "/botTOKEN/sendDocument" and b"%PDF" in sent[0].content


# --- needs Postgres (TRAWL_TEST_DATABASE_URL) ---


def test_tailors_good_fits_once_per_resume_and_sends_them_once(store, board_http) -> None:
    store.upsert(
        [
            Posting(
                "ashby",
                str(i),
                f"Co{i}",
                "Forward Deployed Engineer",
                f"https://a/{i}",
                "Singapore",
                None,
                description="Build with customers.",
            )
            for i in (1, 2)
        ]
    )
    m = lambda s: {"score": s, "strengths": [], "gaps": [], "angle": "x"}  # noqa: E731
    process(store, Criteria(), board_http, FakeClient(job_facts(), matches=[m(80), m(40)]), ("v1", "h1"))
    resume = parse(TEX, BANK)
    client = FakeClient(tailorings=[choices().model_dump()])
    done, failed = tailor_new(store, client, resume, "r1", fake_compiler(99), min_score=60, limit=10)
    assert (done, failed) == (1, [])  # the 40 isn't tailored
    assert tailor_new(store, client, resume, "r1", fake_compiler(99), min_score=60, limit=10) == (0, [])
    assert len(client.requests) == 1
    assert "0000" not in client.requests[0]["messages"][0]["content"]

    ids = [r.id for r in store.undigested().rows]
    apps = store.unsent_applications(ids)
    assert [a.company for a in apps] == ["Co1"] and apps[0].resume_pdf == b"%PDF"
    store.mark_application_sent(apps[0].posting_id)
    assert store.unsent_applications(ids) == []
    # a new résumé (or bank) means tailoring again, for jobs not yet sent
    assert tailor_new(store, client, resume, "r2", fake_compiler(99), min_score=60, limit=10)[0] == 1
    store.mark_digested(store.undigested())
    assert tailor_new(store, client, resume, "r3", fake_compiler(99), min_score=60, limit=10)[0] == 0


def test_edits_list_what_really_changed() -> None:
    resume = parse(TEX, BANK)
    assert edits(resume, identity(resume)) == ["no changes"]
    plan = apply(resume, choices())
    assert edits(resume, plan) == [
        "entries reordered",
        "reworded: e2.b1",
        "spare used: e1.x1",
        "left out: e1.b1, e1.b3",
        "skill rows reordered",
    ]


def test_the_summary_may_name_the_job() -> None:
    resume = parse(TEX)
    t = choices(summary="NTU M.Sc. student, focused on **LLM agents**. Seeking Forward Deployed roles.")
    assert apply(resume, t).notes[0].startswith("summary kept")
    assert apply(resume, t, "Forward Deployed Engineer").notes == []
