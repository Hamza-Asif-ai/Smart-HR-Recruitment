# Smart HR Recruitment - Multi-Agent System (Google ADK)

**Google AI Agents Capstone Project · Track: _Agents for Business_**

An end-to-end recruitment pipeline automated by **four specialized AI agents**
built on the **Google Agent Development Kit (ADK)**. Feed it a job description
and a folder of resumes; get back a ranked shortlist, personalized interview
invitations, and a hiring-insights report.

The pipeline runs **offline and deterministically** without an API key. With a
Gemini key, `run_pipeline(use_agents=True)` / `python -m src.orchestrator --agents`
lets four ADK `LlmAgent`s orchestrate the same deterministic stage tools through
a `SequentialAgent`; if the model path is unavailable or fails validation, the
deterministic runner is used automatically.

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
   `SequentialAgent`. In the optional agentic path each agent calls exactly one
   deterministic stage tool, and full results are passed through shared session
   state (`jd_requirements → ranked_candidates → interview_emails → insights`).
   The model only sees small, PII-free summaries; ranking and drafting remain in
   Python.

2. **MCP Server integration** — `src/mcp_filesystem_server.py` exposes
   *sandboxed* file tools (`list_resumes`, `read_resume`,
   `read_job_description`, `save_report`) with path-traversal protection.
   Agents reach the filesystem only through this boundary.

3. **Agent Skills** — reusable, testable tools. `resume_parser_skill`,
   `candidate_scorer_skill`, and `email_drafter_skill` (plus `jd_parser_skill`
   and `suggest_interview_slots`) do the work; `adk_stages.py` exposes the four
   pipeline stages as ADK `FunctionTool`s.

4. **Security / PII masking + RBAC** — `src/security.py` masks emails, phones,
   and URLs before logging, and enforces role-based access control so only
   `recruiter`/`admin` roles can view unmasked PII or send invitations. Every
   gated action is written to an audit log. Resume text is sanitised before
   parsing, and the optional LLM path receives counts rather than resume text,
   so prompt injection cannot reach a model.

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

  Default: deterministic orchestrator (no API key)
  Optional: the same four stages as ADK LlmAgents + FunctionTools

  Cross-cutting:  ├─ MCP Filesystem Server  (read resumes / save reports)
                  └─ Security layer          (PII masking in logs + RBAC)

  Stage tools: adk_jd_stage · adk_screening_stage · adk_scheduler_stage · adk_insights_stage
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
│   ├── adk_stages.py                   ← ADK stage tools (deterministic core)
│   ├── orchestrator.py                 ← deterministic runner + optional ADK path
│   ├── config.py                       ← env-driven thresholds + timezone
│   └── tracing.py                      ← trace recorder
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
# With Gemini, try the ADK-orchestrated path (falls back automatically):
HR_DATA_ROOT=./data python -m src.orchestrator --agents
```

Or from Python:

```python
from src.orchestrator import run_pipeline
result = run_pipeline(role="recruiter")                 # deterministic
# result = run_pipeline(role="recruiter", use_agents=True)  # ADK + fallback
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
- `GOOGLE_API_KEY` — enables the optional ADK pipeline (`--agents` /
  `use_agents=True`); the Gradio inbox tool stays deterministic.
- `HR_DATA_ROOT` — points at a different data directory.
- `SHORTLIST_THRESHOLD` / `REVIEW_THRESHOLD` — shortlist and review cut-offs
  (default 75 / 55, validated 0–100 and review ≤ shortlist).
- `INTERVIEW_TIMEZONE` — IANA timezone for proposed slots (default
  `Asia/Kolkata`; falls back to UTC if invalid).

Email credentials are entered in the UI at runtime and are not written to disk.
Use test accounts only.

---

## Sample output

Current deterministic baseline on the 18 synthetic resumes (top 8 by match %):

| Rank | Candidate | Source | Match % | Recommendation |
|------|-----------|--------|---------|----------------|
| 1 | Marcus Bell | txt | 93.6 | Strong match - shortlist |
| 2 | Daniel Okafor | txt | 91.1 | Strong match - shortlist |
| 3 | Sofia Martinez | **PDF** | 89.7 | Strong match - shortlist |
| 4 | Jose Alvarez | txt | 88.3 | Strong match - shortlist |
| 5 | Olivia Brown | txt | 82.2 | Strong match - shortlist |
| 6 | Chen Wei | txt | 79.2 | Strong match - shortlist |
| 7 | Nadia Petrova | txt | 79.2 | Strong match - shortlist |
| 8 | Emma Jones | **PDF** | 78.3 | Strong match - shortlist |

Funnel: 18 applicants → 10 shortlisted (55.6%) → 10 invitations drafted; average
match 64.9%. One scanned-style PDF had no extractable text and is flagged for
manual review instead of being ranked as a candidate.

> Scoring is evidence-weighted: half of the required-skill score comes from
> skills demonstrated in the experience section, so a resume that only lists
> keywords is flagged and does not get shortlisted. Required skills are read
> only from explicit requirements sections (responsibilities are excluded), and
> an any-of group such as AWS/Azure/GCP is satisfied by any one provider.
> Nice-to-have matches contribute at most 10%. Experience ranges are merged and
> education dates are ignored.

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

There are two paths:

- **Default (no API key):** `src/orchestrator.py` runs the four stages directly.
  It is deterministic, testable, and used by the Gradio inbox tool.
- **Optional ADK path:** `run_pipeline(use_agents=True)` or
  `python -m src.orchestrator --agents` runs the four `LlmAgent`s in a
  `SequentialAgent`. Each calls one `adk_stages.py` tool, which delegates to the
  deterministic functions and stores full results in ADK session state. The
  result is validated (all resumes screened, valid ranks, numeric scores) before
  it is accepted; otherwise the deterministic path is used.

`src/agents.py` contains only the four production agents wired into the pipeline.

Supporting infrastructure exists for:

- **Human-in-the-loop** email approval in the Gradio UI
- **Evidence and confidence signals** on ranked candidates
- **Observability** (`src/tracing.py`)

These are incremental improvements, not claims of a fully autonomous system.

---

## Tech stack

`google-adk` · `google-genai` (Gemini) · `mcp` · `pandas` · `matplotlib` ·
`pypdf` · `gradio` · `pytest`

*Built for the Google AI Agents Capstone - "Agents for Business" track.*
