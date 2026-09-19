# 🤖 Smart HR Recruitment — Multi-Agent System (Google ADK)

**Google AI Agents Capstone Project · Track: _Agents for Business_**

An end-to-end recruitment pipeline automated by **four specialized AI agents**
built on the **Google Agent Development Kit (ADK)**. Feed it a job description
and a folder of resumes; get back a ranked shortlist, personalized interview
invitations, and a visual hiring-insights dashboard.

![dashboard](data/output/dashboard.png)

---

## ✨ What it does

| # | Agent | Input → Output |
|---|-------|----------------|
| 1 | **JD Agent** | Raw job description → structured requirements (skills, seniority, min years, qualifications) as JSON |
| 2 | **Resume Screener Agent** | Resumes (PDF/text) → parsed profiles, scored & ranked against the JD with a match % |
| 3 | **Interview Scheduler Agent** | Shortlist → personalized invitation emails + proposed interview slots |
| 4 | **Insights Agent** | Full funnel → hiring metrics + a visual dashboard and markdown report |

---

## 🧩 Concepts demonstrated (capstone requires ≥ 3 — this implements 4)

1. **Multi-agent systems with ADK** — the four agents are composed into an ADK
   `SequentialAgent`. Each writes to an `output_key`, so its result becomes the
   next agent's input through shared session state:
   `jd_requirements → ranked_candidates → interview_emails → insights_report`.

2. **MCP Server integration** — `src/mcp_filesystem_server.py` is a
   **Model Context Protocol** server exposing *sandboxed* file tools
   (`list_resumes`, `read_resume`, `read_job_description`, `save_report`) with
   path-traversal protection. Agents reach the filesystem only through it.

3. **Agent Skills** — reusable, testable tools wrapped as ADK `FunctionTool`s:
   `resume_parser_skill`, `candidate_scorer_skill`, `email_drafter_skill`
   (plus `jd_parser_skill`, `suggest_interview_slots`).

4. **Security / PII masking + RBAC** — `src/security.py` masks emails, phones,
   and URLs before anything is logged (via a logging `Filter`), and enforces
   **role-based access control** so only `recruiter`/`admin` roles can view
   unmasked PII or send invitations. Every gated action is written to an audit log.

---

## 🏗️ Architecture

```
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

## 📁 Project structure

```
smart-hr-recruitment/
├── README.md                         ← you are here
├── requirements.txt
├── notebook/
│   └── smart_hr_recruitment.ipynb    ← MAIN Kaggle deliverable (self-contained)
├── src/
│   ├── security.py                   ← PII masking + RBAC (cross-cutting)
│   ├── skills.py                     ← reusable Agent Skills
│   ├── mcp_filesystem_server.py      ← MCP server (sandboxed file tools)
│   ├── agents.py                     ← the 4 ADK LlmAgents + SequentialAgent
│   └── orchestrator.py               ← end-to-end pipeline runner
├── data/
│   ├── job_descriptions/             ← sample JD
│   ├── resumes/                      ← 5 sample resumes
│   └── output/                       ← generated report, dashboard, JSON
└── assets/
```

---

## 🚀 How to run

> 🆕 **New here?** See **[SETUP.md](SETUP.md)** for full step-by-step install & run instructions (Windows-focused, with troubleshooting).

### Option A — Kaggle (recommended, the graded deliverable)
1. Upload `notebook/smart_hr_recruitment.ipynb` to Kaggle (or *File → Import Notebook*).
2. *(Optional)* Add a Gemini API key under **Add-ons → Secrets** as `GOOGLE_API_KEY`
   to run the live ADK `LlmAgent`s.
3. **Run All.** The notebook is self-contained: it writes the source modules and
   sample data, then runs every agent and the full pipeline.

> Without an API key the notebook automatically uses the **deterministic skill
> runner**, so every cell still produces real output — ideal for reproducible grading.

### Option B — Locally

```bash
pip install -r requirements.txt
cd smart-hr-recruitment

# Run the full pipeline end-to-end
HR_DATA_ROOT=./data python -m src.orchestrator        # (or import run_pipeline)
python -c "from src.orchestrator import run_pipeline; run_pipeline('recruiter')"

# Run the MCP filesystem server standalone (stdio transport)
python -m src.mcp_filesystem_server --root ./data
```

---

## 📊 Sample output

**Ranked candidates** (Senior Backend Engineer role, 6 applicants — one from a PDF):

| Rank | Candidate | Source | Match % | Recommendation |
|------|-----------|--------|---------|----------------|
| 1 | Daniel Okafor  | txt | 77.5 | Strong match - shortlist |
| 2 | Priya Sharma   | txt | 75.0 | Strong match - shortlist |
| 3 | Sofia Martinez | **PDF** | 72.5 | Possible match - shortlist |
| 4 | Chen Wei       | txt | 68.8 | Possible match - review |
| 5 | Arjun Mehta    | txt | 48.8 | Weak match - likely reject |
| 6 | Fatima Khan    | txt | 27.5 | Weak match - likely reject |

**Interview invitation (excerpt):**
```
Subject: Interview Invitation - Senior Backend Engineer (Python / Cloud) at NimbusPay Technologies

Dear Daniel,
Thank you for applying ... we were impressed by your experience with aws, ci/cd,
docker and would like to invite you to an interview.
Proposed slot: Monday, 29 Jun 2026 at 10:00 IST ...
```

Full artifacts are in [`data/output/`](data/output/):
`ranked_candidates.json`, `interview_emails.json`, `recruitment_insights.md`, `dashboard.png`.

---

## 🔐 Security notes

- **PII masking** is applied at the logging layer (`PiiMaskingFilter`) *and* at
  the data layer, so candidate emails/phones never appear in logs in clear text.
- **RBAC** roles: `admin`, `recruiter`, `hiring_manager`, `viewer`. A `viewer`
  attempting `read_resume`/`send_invitations` is blocked with `AccessDeniedError`;
  unprivileged roles see PII masked even in returned text.
- All access decisions are recorded in `SecurityContext.audit_log`.

---

## 🧪 Dataset

Ships with a small inline sample dataset (1 JD + 6 resumes — **5 text + 1 PDF**)
styled after the public
**[Resume Dataset by gauravduttakiit](https://www.kaggle.com/datasets/gauravduttakiit/resume-dataset)**
on Kaggle. The MCP `read_resume` tool extracts text from PDFs via `pypdf`
transparently, so mixed-format folders work out of the box. To use the real
dataset, add it via *Add data* and point `RESUME_DIR` at the input path.

---

## 🛠️ Tech stack

`google-adk` · `google-genai` (Gemini) · `mcp` · `pandas` · `matplotlib`

*Built for the Google AI Agents Capstone — "Agents for Business" track.*
