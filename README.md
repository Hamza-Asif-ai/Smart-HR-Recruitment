# Smart HR Recruitment - Multi-Agent System (Google ADK)

**Google AI Agents Capstone Project · Track: _Agents for Business_**

An end-to-end recruitment pipeline automated by **four specialized AI agents**
built on the **Google Agent Development Kit (ADK)**. Feed it a job description
and a folder of resumes; get back a ranked shortlist, personalized interview
invitations, and a hiring-insights report.

The pipeline runs **offline and deterministically** without an API key. With a
Gemini key, the same four agents can be driven by ADK `LlmAgent`s through a
`SequentialAgent`.

---

## What it does

| # | Agent | Input → Output |
|---|-------|----------------|
| 1 | **JD Agent** | Raw job description → structured requirements (skills, seniority, min years, qualifications) as JSON |
| 2 | **Resume Screener Agent** | Resumes (PDF/text) → parsed profiles, scored and ranked against the JD with a match % |
| 3 | **Interview Scheduler Agent** | Shortlist → personalized invitation drafts + proposed interview slots |
| 4 | **Insights Agent** | Full funnel → hiring metrics and a markdown report |

---

## Concepts demonstrated

1. **Multi-agent systems with ADK** — the four agents are composed into an ADK
   `SequentialAgent`. Each writes to an `output_key`, so its result becomes the
   next agent's input through shared session state:
   `jd_requirements → ranked_candidates → interview_emails → insights_report`.

2. **MCP Server integration** — `src/mcp_filesystem_server.py` exposes
   *sandboxed* file tools (`list_resumes`, `read_resume`,
   `read_job_description`, `save_report`) with path-traversal protection.
   Agents reach the filesystem only through this boundary.

3. **Agent Skills** — reusable, testable tools wrapped as ADK `FunctionTool`s:
   `resume_parser_skill`, `candidate_scorer_skill`, `email_drafter_skill`
   (plus `jd_parser_skill` and `suggest_interview_slots`).

4. **Security / PII masking + RBAC** — `src/security.py` masks emails, phones,
   and URLs before logging, and enforces role-based access control so only
   `recruiter`/`admin` roles can view unmasked PII or send invitations. Every
   gated action is written to an audit log. Resume text is sanitised before it
   reaches an LLM to reduce prompt-injection risk.

---

## Architecture

```text
                         ┌──────────────────────────────────────────┐
                         │          SequentialAgent (ADK)            │
                         │     smart_hr_recruitment_pipeline         │
                         └──────────────────────────────────────────┘
  job_description ─▶ [JD Agent] ─jd_requirements▶ [Resume Screener]
                                                         │ ranked_candidates
                                                         ▼
                          insights_report ◀─ [Insights] ◀─ [Scheduler]
                                                         interview_emails

  Cross-cutting:  ├─ MCP Filesystem Server  (read resumes / save reports)
                  └─ Security layer          (PII masking in logs + RBAC)

  Agent Skills (FunctionTools): resume_parser · candidate_scorer · email_drafter
```

---

## Project structure

```text
smart-hr-recruitment/
├── README.md
├── SETUP.md
├── SECURITY.md                         ← data policy, secrets, history purge
├── requirements.txt                    ← pinned runtime dependencies
├── requirements-dev.txt                ← + pytest
├── app.py                              ← Gradio demo UI
├── notebook/
│   └── smart_hr_recruitment.ipynb      ← imports src/, no duplicated code
├── src/
│   ├── security.py                     ← PII masking + RBAC
│   ├── skills.py                       ← reusable Agent Skills
│   ├── sanitize.py                     ← prompt-injection defense
│   ├── mcp_filesystem_server.py        ← sandboxed MCP file tools
│   ├── agents.py                       ← the 4 ADK LlmAgents + SequentialAgent
│   ├── orchestrator.py                 ← deterministic end-to-end runner
│   ├── memory.py                       ← optional vector memory (JSON fallback)
│   ├── tracing.py                      ← trace recorder
│   └── budget.py                       ← LLM/time budget guard
├── data/
│   ├── job_descriptions/               ← one synthetic JD
│   └── resumes/                        ← 18 synthetic resumes (15 txt + 3 PDF)
├── tools/
│   ├── generate_synthetic_resumes.py   ← rebuild the fixtures
│   └── check_no_pii.py                 ← CI PII/secret gate
├── tests/                              ← offline pytest suite
└── .github/workflows/ci.yml            ← PII scan + tests
```

`data/output/` is generated at runtime and gitignored.

---

## How to run

### Local pipeline (no API key needed)

```bash
python -m venv venv
# Windows: venv\Scripts\activate
source venv/bin/activate
pip install -r requirements-dev.txt

HR_DATA_ROOT=./data python -m src.orchestrator
```

Or from Python:

```python
from src.orchestrator import run_pipeline
result = run_pipeline(role="recruiter")
```

### Notebook

The notebook **imports the real modules from `src/`** and uses the shared
synthetic dataset. It no longer writes duplicate source files.

```bash
pip install -r requirements-dev.txt
jupyter notebook notebook/smart_hr_recruitment.ipynb
```

Run it from the repository root (the first cell locates the root automatically).
For Kaggle, upload the **whole repository** as a dataset or clone it into the
notebook session; uploading only the `.ipynb` is not enough because it imports
`src/`.

### Gradio UI

```bash
python app.py
```

Optional environment variables:

- `BROWSER_STATE_SECRET` — enables Gradio `BrowserState`; without it the app
  falls back to in-memory `gr.State`.
- `GOOGLE_API_KEY` — enables live Gemini agents.
- `HR_DATA_ROOT` — points at a different data directory.

Email credentials are entered in the UI at runtime and are not written to disk.
Use test accounts only.

---

## Sample output

Current deterministic baseline on the 18 synthetic resumes (top 8 by match %):

| Rank | Candidate | Source | Match % | Recommendation |
|------|-----------|--------|---------|----------------|
| 1 | Priya Menon | txt | 92.5 | Strong match - shortlist |
| 2 | Daniel Okafor | txt | 87.5 | Strong match - shortlist |
| 3 | Jose Alvarez | txt | 85.0 | Strong match - shortlist |
| 4 | Nadia Petrova | txt | 82.5 | Strong match - shortlist |
| 5 | Marcus Bell | txt | 81.2 | Strong match - shortlist |
| 6 | Sofia Martinez | **PDF** | 76.2 | Strong match - shortlist |
| 7 | Olivia Brown | txt | 73.8 | Possible match - review |
| 8 | Emma Jones | **PDF** | 70.0 | Possible match - review |

Funnel: 18 applicants → 8 shortlisted (44.4%) → 8 invitations drafted; average
match 62.2%.

> The baseline scores by required-skill overlap. The dataset deliberately
> includes a keyword stuffer, so scoring hardening is tracked as a later phase.

Artifacts are written to `data/output/` at runtime:
`recruitment_insights.md` (and JSON/dashboard artifacts when generated by the
notebook or UI). This directory is gitignored.

---

## Dataset

The repository ships **18 fully synthetic resumes** plus one synthetic job
description. No real candidate data is included. The fixtures cover:

- fresh graduate and junior developer
- career gap and career changer
- keyword stuffer
- non-English name and Spanish-language content
- image-only **scanned-style PDF** (no text layer)
- PDF with **white-on-white hidden text**
- resume carrying an **"ignore previous instructions"** prompt injection

All emails use `@example.com`; all phone numbers use the `555` exchange. Rebuild
the fixtures with:

```bash
python tools/generate_synthetic_resumes.py
```

Run the PII gate before committing:

```bash
python tools/check_no_pii.py --all
```

---

## Security

See **[SECURITY.md](SECURITY.md)** for the full policy. Highlights:

- Real PII and secrets are blocked by `tools/check_no_pii.py` and CI.
- Logs pass through `PiiMaskingFilter`; truncation uses `safe_preview()`.
- RBAC: `admin`, `recruiter`, `hiring_manager`, `viewer`.
- Resume text is sanitised before LLM use.
- If real data was ever committed, deleting it is not enough — purge Git
  history and rotate exposed credentials.

---

## Agentic architecture

The default pipeline is the four-agent `SequentialAgent` above. `src/agents.py`
also contains builder functions for a planner, reflection, email-writer,
reply-classifier, and supervisor agent. These are **experimental scaffolding**
and are not wired into the default pipeline yet; `src/orchestrator.py` includes
a planner entry point that currently falls back to the deterministic pipeline.

Supporting infrastructure exists for:

- **Human-in-the-loop** email approval in the Gradio UI
- **Confidence scores** on candidates
- **Persistent memory** (`src/memory.py`; ChromaDB optional, JSON fallback)
- **Observability** (`src/tracing.py`)
- **Budget controls** (`src/budget.py`)

These are incremental improvements, not claims of a fully autonomous system.

---

## Tech stack

`google-adk` · `google-genai` (Gemini) · `mcp` · `pandas` · `matplotlib` ·
`pypdf` · `gradio` · `pytest`

*Built for the Google AI Agents Capstone - "Agents for Business" track.*
