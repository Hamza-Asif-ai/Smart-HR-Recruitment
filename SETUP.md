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
| `google-adk` | Google Agent Development Kit — base for the 4 agents | ✅ Core |
| `google-genai` | Gemini API client (for live LLM agents) | ✅ Core |
| `mcp` | MCP filesystem server (sandboxed file tools) | ✅ Core |
| `pandas` | Data handling (notebook) | ✅ |
| `matplotlib` | Builds the dashboard image | ✅ |
| `pypdf` | Parses the PDF resume (Sofia Martinez) | ⚠️ Needed for the PDF resume |

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

---

## 6. (Optional) Gemini API key — only for LIVE LLM agents

Without a key the project automatically uses the **deterministic skill runner**, so the
full pipeline still runs and produces real output. To run the live Gemini `LlmAgent`s:

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
Open `notebook/smart_hr_recruitment.ipynb` in the browser and choose **Run All**.
On Kaggle it's self-contained — just upload and *Run All*.

---

## 8. Full sequence (copy-paste)

**Windows / PowerShell**
```powershell
cd "C:\Users\<YourName>\smart-hr-recruitment"
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
$env:HR_DATA_ROOT="./data"
python -m src.orchestrator
```

**Linux / Mac**
```bash
cd smart-hr-recruitment
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
HR_DATA_ROOT=./data python3 -m src.orchestrator
```

---

## 9. Output

After a successful run, find the artifacts in **`data/output/`**:

| File | What it is |
|------|-----------|
| `ranked_candidates.json` | All candidates scored & ranked against the JD |
| `interview_emails.json` | Personalized interview invitations for the shortlist |
| `recruitment_insights.md` | Funnel metrics + ranked candidates table |
| `dashboard.png` | Visual dashboard (match scores · funnel · top skills) |

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
