# ⚙️ Setup Guide — Smart HR Recruitment

A step-by-step guide to run this project from scratch on a **new machine**.
Primary instructions are for **Windows**; Linux/Mac equivalents are noted where they differ.

> 💡 **TL;DR (Windows / PowerShell)** — copy-paste the block in [Section 8](#8-full-sequence-copy-paste).

---

## 1. Prerequisites (install these first)

| Tool | Why it's needed | Verify with |
|------|-----------------|-------------|
| **Python 3.10+** (3.12 recommended) | The whole project runs on Python | `python --version` |
| **pip** | Installs the libraries | `pip --version` |
| **git** | Clone the repo (optional if you copy the folder) | `git --version` |

**Windows install notes**
1. Download Python **3.12** from <https://www.python.org/downloads/>.
2. In the installer, **tick "Add python.exe to PATH"** *before* clicking *Install Now*. (Critical — without it, `python` won't be found.)
3. Install git from <https://git-scm.com/download/win> if you don't have it.

> If `python` doesn't work, try `py` (e.g. `py --version`).

---

## 2. Get the project onto the machine

```powershell
# Option A — clone from GitHub
git clone <repo-url>
cd smart-hr-recruitment

# Option B — folder copied via USB/zip: just cd into it
cd "C:\Users\<YourName>\smart-hr-recruitment"
```

Replace `<YourName>` with your actual Windows username.

---

## 3. Create & activate a virtual environment

Keeps project libraries isolated from system Python.

**PowerShell**
```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

> If PowerShell says *"running scripts is disabled on this system"*, run this once, then activate again:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> venv\Scripts\Activate.ps1
> ```

**CMD**
```cmd
python -m venv venv
venv\Scripts\activate.bat
```

**Linux / Mac**
```bash
python3 -m venv venv
source venv/bin/activate
```

When active, your prompt shows `(venv)` at the start.

---

## 4. Install the libraries

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

This installs everything the project needs:

| Library | Purpose | Required? |
|---------|---------|-----------|
| `google-adk` | Google Agent Development Kit — base for the 4 agents | Core |
| `google-genai` | Gemini API client (for live LLM agents) | Core |
| `mcp` | MCP filesystem server (sandboxed file tools) | Core |
| `pandas` | Data handling (notebook) | Core |
| `matplotlib` | Builds the dashboard image | Core |
| `pypdf` | Parses PDF resumes (normal, scanned, hidden-text) | Core |
| `gradio` | Local demo UI (`app.py`) | Core |
| `pillow` | Generates the synthetic PDF fixtures | Core |

Versions are pinned in `requirements.txt`. `requirements-dev.txt` adds `pytest`.

---

## 5. Run the pipeline

On Windows you must set the data-root variable **first**, then run (the Linux inline
`HR_DATA_ROOT=./data python ...` form does **not** work in PowerShell/CMD).

**PowerShell**
```powershell
$env:HR_DATA_ROOT="./data"
python -m src.orchestrator
```

**CMD**
```cmd
set HR_DATA_ROOT=./data
python -m src.orchestrator
```

**Linux / Mac**
```bash
HR_DATA_ROOT=./data python3 -m src.orchestrator
```

Run as an importable function instead:
```powershell
python -c "from src.orchestrator import run_pipeline; run_pipeline('recruiter')"
```

Run the MCP filesystem server standalone:
```powershell
python -m src.mcp_filesystem_server --root ./data
```

Optional tuning variables (sensible defaults are built in):

| Variable | Default | Purpose |
|---|---|---|
| `SHORTLIST_THRESHOLD` | `75` | Minimum match % to shortlist |
| `REVIEW_THRESHOLD` | `45` | Minimum match % for manual review |
| `INTERVIEW_TIMEZONE` | `Asia/Kolkata` | IANA timezone for proposed slots |

Set them the same way as `HR_DATA_ROOT` (for example
`$env:SHORTLIST_THRESHOLD="80"`).

---

## 6. (Optional) Gemini API key — only for LIVE LLM agents

Without a key the project automatically uses the **deterministic skill runner**, so the
full pipeline still runs and produces real output. Phase 3 also requires a model id:

```powershell
$env:GEMINI_MODEL="your-available-model-id"   # required for Phase 3
$env:GOOGLE_API_KEY="your-key-here"
```

Startup validates the model with the Gemini API. The quarantined extractor is
the only LLM that receives resume text (PII-minimised), and every claim must
quote the source. Model failure, invalid JSON, or budget exhaustion falls back
to regex extraction with a visible banner. The legacy ADK path can still be run
with `python -m src.orchestrator --agents`.

Budgets and review thresholds (optional):

```powershell
$env:MAX_LLM_CALLS="24"
$env:MAX_TOKENS="60000"
$env:MAX_SECONDS="120"
$env:SKILL_DISAGREEMENT_THRESHOLD="0.35"
$env:REPLY_CONFIDENCE_THRESHOLD="0.65"
$env:HR_MEMORY_HMAC_KEY="a-long-random-secret"  # stable across restarts
```

1. Get a free key from <https://aistudio.google.com>.
2. Set it as an environment variable:

   **PowerShell (current session only)**
   ```powershell
   $env:GOOGLE_API_KEY="your-key-here"
   ```
   **PowerShell (permanent — reopen terminal after)**
   ```powershell
   setx GOOGLE_API_KEY "your-key-here"
   ```
   **Linux / Mac**
   ```bash
   export GOOGLE_API_KEY="your-key-here"
   ```
3. **On Kaggle:** Add-ons → Secrets → add as `GOOGLE_API_KEY`.

---

## 7. (Optional) Run the notebook

```powershell
pip install jupyter
jupyter notebook
```

Open `notebook/smart_hr_recruitment.ipynb` from the **repository root** and
choose **Run All**. The notebook imports the real modules from `src/` and uses
`data/resumes/`; it does not write duplicate copies.

For Kaggle, upload the **whole repository** as a dataset (or clone it in the
session) and run the notebook from that directory. Uploading only the `.ipynb`
will fail because `src/` is required.

---

## 8. Full sequence (copy-paste)

**Windows / PowerShell**
```powershell
cd "C:\Users\<YourName>\smart-hr-recruitment"
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
$env:HR_DATA_ROOT="./data"
python -m src.orchestrator
python tools/check_no_pii.py --all
pytest -q
```

**Linux / Mac**
```bash
cd smart-hr-recruitment
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
HR_DATA_ROOT=./data python3 -m src.orchestrator
python tools/check_no_pii.py --all
pytest -q
```

---

## 9. Output

`data/output/` is created at runtime and is gitignored.

The deterministic pipeline writes:

| File | What it is |
|------|-----------|
| `recruitment_insights.md` | Funnel metrics + ranked candidates table |

The notebook and Gradio UI additionally produce `ranked_candidates.json`,
`interview_emails.json`, and `dashboard.png` when those flows are used.

---

## 10. Common issues (Windows)

| Problem | Fix |
|---------|-----|
| `python` not recognized | Reinstall Python with **"Add to PATH"** ticked, or use `py` instead. |
| `Activate.ps1 cannot be loaded` | Run `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, then activate again. |
| `ModuleNotFoundError` after install | Make sure the venv is **active** (prompt shows `(venv)`) before running. |
| Pipeline can't find data | Set `$env:HR_DATA_ROOT="./data"` in the **same** terminal session before running. |
| `venv` / env var "lost" in a new terminal | Both are session-specific — re-activate the venv and re-set `HR_DATA_ROOT` in every new terminal. |

---

*Need the live LLM agents? See Section 6. Otherwise everything runs offline in deterministic mode.*


## Connect Gmail and Calendar (optional)

1. Create a Google Cloud OAuth client of type **Web application**.
2. Add `http://localhost:8080/oauth2callback` as an authorized redirect URI.
3. Set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`, then run:

```bash
python -m src.google_oauth
```

The flow requests only `gmail.readonly` and `calendar.freebusy`. The refresh
token is saved to the git-ignored `data/output/google_oauth_token.json`. For a
container or cloud host, set `GOOGLE_OAUTH_REFRESH_TOKEN` instead of shipping the
token file. Interview invitations are still sent through Brevo after approval.

### Docker / Render

```bash
docker build -t smart-hr-recruitment .
docker run -p 10000:10000 -e HR_USERS='...' smart-hr-recruitment
```

`HR_USERS` must be set in containers so the app binds publicly with login
enabled. `render.yaml` deploys the same Dockerfile; add the OAuth and Gemini
secrets in the Render dashboard.


## Environment Variables (Agentic Features)

- `GOOGLE_API_KEY` / `GEMINI_API_KEY` - Google Gemini API key for Phase 3 and
  the optional ADK pipeline. Without it, the deterministic offline pipeline is
  used.
- `GEMINI_MODEL` - Model id for the Phase 3 extractor. No model name is
  hardcoded; an unavailable id fails startup with a clear error.
- `MAX_LLM_CALLS`, `MAX_TOKENS`, `MAX_SECONDS` - Per-run budgets. Exceeding any
  of them degrades to deterministic mode and shows a banner.
- `SKILL_DISAGREEMENT_THRESHOLD` - LLM-vs-regex review threshold (default 0.35).
- `REPLY_CONFIDENCE_THRESHOLD` - Reply auto-triage threshold (default 0.65).
- `HR_MEMORY_DB`, `HR_MEMORY_HMAC_KEY` - Advisory SQLite memory path and stable
  HMAC key. Raw candidate emails are never stored.
- `HR_APPROVAL_QUEUE_MAX_SIZE` - Maximum pending drafts awaiting approval.
- `BROWSER_STATE_SECRET` - Enables Gradio `BrowserState`; without it the UI uses
  session-only `gr.State`.
- `HR_DATA_ROOT` - Data directory (default: `./data`).
- `HR_USERS` - UI accounts as `username:role:pbkdf2_hash` entries separated by
  `;`, or JSON. Generate a hash with
  `python -m src.auth --hash-password 'your-password'`. The app refuses to start
  without this variable.
- `HR_ALLOW_NO_AUTH` - Set to `1` for local development without `HR_USERS`. The
  app then listens on `127.0.0.1` only. Never use this on a public host.
- `HR_AUTH_MAX_FAILURES`, `HR_AUTH_LOCKOUT_SECONDS` - Login lockout tuning
  (defaults: 5 failures, 300 seconds).
- `HR_AUTH_AUDIT_LOG` - Path for the JSONL authentication audit log.
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` - Google OAuth client
  used for the read-only Gmail and Calendar scopes.
- `GOOGLE_OAUTH_REDIRECT_URI` - OAuth callback (default
  `http://localhost:8080/oauth2callback`).
- `GOOGLE_OAUTH_REFRESH_TOKEN` - Refresh token supplied as a secret instead of a
  token file.
- `GOOGLE_OAUTH_TOKEN_FILE` - Override the git-ignored token path.
- `CALENDAR_ID`, `CALENDAR_LOOKAHEAD_DAYS`, `INTERVIEW_SLOT_MINUTES` - Calendar
  used for free/busy, the lookahead window, and interview length.
- `HR_WORKSPACE_FILE` - Saved multi-job workspace JSON path.
