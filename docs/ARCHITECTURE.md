# Architecture — Phase 3

The system is split into a **small untrusted-text LLM surface**, a
**deterministic decision core**, and a **human approval gate**. A model may
extract claims, critique, draft, or classify; it may never score, persist PII,
or send email.

```mermaid
flowchart TD
    UI[Gradio UI / CLI] --> AUTH[RBAC + session RunContext]
    AUTH --> C[Recruitment Coordinator<br/>ADK LlmAgent]
    C --> JD[JD Analyst<br/>role-bound tool]
    C --> P[Parallel Screeners<br/>per resume]
    C --> CR[Critic LoopAgent<br/>max 2 iterations]
    C --> EW[Email Writer<br/>draft only]
    C --> RT[Reply Triage<br/>schema + confidence]

    RAW[Untrusted resume text] --> X[Quarantined Extractor<br/>no tools, output_schema]
    X --> V[Quote verification<br/>drop unsupported claims]
    P --> V
    V --> S[Deterministic scorer<br/>evidence weights + Jaccard disagreement]
    S --> CONF[Confidence + review flags]
    CONF --> Q[Human approval queue<br/>preview / edit / approve]
    Q -->|approved only| M[Deterministic Brevo mailer]

    AUTH --> B[RunBudget<br/>calls / tokens / steps / seconds]
    B -->|exceeded| D[Deterministic degradation<br/>visible banner]
    S --> MEM[(SQLite memory<br/>HMAC(email) only)]
    MEM -. advisory hint, never score .-> S
    AUTH --> T[(Bounded trace ring<br/>PII-masked spans)]
```

## Trust boundaries

| Boundary | Rule |
|---|---|
| Resume/JD/reply text | Untrusted DATA; never instructions |
| Quarantined extractor | No tools, no filesystem, no mail; schema-only output |
| Every extractor claim | Must cite a literal quote; unverified claims are dropped |
| Coordinator and specialists | Consume validated profiles/summaries, not arbitrary attachments |
| Tools | Explicit allow-list, role/capability check, CR/LF/NUL rejection |
| Email | Draft schema has no send path; approval queue is required |
| Memory | Store `HMAC-SHA256(normalized_email)`; raw email never persisted |
| Trace | Bounded, PII/secret-masked, exportable per run |

## Components

- `src/coordinator.py` — coordinator plus JD analyst, screener(s), critic loop,
  draft-only email writer, and reply triage. The extractor is a separate no-tool
  specialist. `ParallelAgent` runs one screener per resume.
- `src/extractor.py` — schema-constrained Gemini extraction, PII minimisation,
  exact-quote verification, model availability check, hidden-PDF-text heuristics.
- `src/models.py` — pydantic schemas (`CandidateProfile`, `EmailDraft`,
  `ReplyTriage`, `CritiqueReport`, …).
- `src/scoring.py` — deterministic scoring over verified claims, Jaccard
  disagreement, calibrated confidence, and `needs_human_review`.
- `src/guardrails.py` — ADK `before/after_model` and `before/after_tool`
  callbacks: budget, unicode/PII normalisation, tool RBAC, trace spans.
- `src/tools.py` — the only model-callable tools. There is no send tool.
- `src/approval.py` — in-memory human approval queue and deterministic mailer
  gate (`send_approved_draft`).
- `src/budget.py` — per-run call/token/step/time budget and degradation banner.
- `src/memory.py` — optional advisory SQLite memory keyed by HMAC(email).
- `src/tracing.py` — per-run, bounded, masked spans; `export_run()`.
- `src/orchestrator.py` — deterministic and Phase 3 fallback runner;
  `run_phase3_pipeline()` degrades safely without blocking the UI.
- `app.py` — ranked candidates → **Preview Invitations for Approval** →
  editable custom template → **Approve & Send** → deterministic mailer.

## Failure and degradation

1. Missing key/model → deterministic regex extraction/scoring; visible banner.
2. `MAX_LLM_CALLS`, `MAX_TOKENS`, `MAX_SECONDS`, or step budget exceeded →
   no further model calls; deterministic results remain available.
3. Invalid/empty model JSON or unavailable model → regex fallback.
4. Extraction disagreement above `SKILL_DISAGREEMENT_THRESHOLD`, injection
   indicators, or hidden PDF text → `needs_human_review=true`.
5. Reply confidence below `REPLY_CONFIDENCE_THRESHOLD` → human review.
6. Unapproved, edited-after-approval, or rejected draft → send is refused.

## Environment

| Variable | Purpose |
|---|---|
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | Gemini access |
| `GEMINI_MODEL` | Required model id; validated at startup |
| `MAX_LLM_CALLS`, `MAX_TOKENS`, `MAX_SECONDS` | Per-run budgets |
| `SKILL_DISAGREEMENT_THRESHOLD` | LLM vs regex review threshold (0–1) |
| `REPLY_CONFIDENCE_THRESHOLD` | Reply auto-triage threshold (0–1) |
| `HR_MEMORY_DB` | SQLite memory path |
| `HR_MEMORY_HMAC_KEY` | Stable memory keying secret |
| `HR_APPROVAL_QUEUE_MAX_SIZE` | Bounded pending drafts |

Model ids are never hardcoded. `extractor.check_model_available()` uses
`client.models.get()` so an invalid `GEMINI_MODEL` fails with a clear message
before a run starts.
