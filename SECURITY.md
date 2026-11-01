# Security Policy

## Data policy

This repository contains **synthetic recruitment data only**.

- Every email address must use the reserved `@example.com` domain.
- Every phone number must use the reserved `555` exchange.
- Names, employers, schools, and profile URLs are invented.
- Do not commit real resumes, real candidate contact details, or real recruiter
  accounts. Put private data outside the repository and point `HR_DATA_ROOT` at
  it locally.

Run the automated check before committing:

```bash
python tools/check_no_pii.py --all
```

The checker scans tracked files (and the working tree with `--all`), including
PDF text, and fails on:

- non-`example.com` email addresses
- phone-like numbers without a `555` exchange
- Google/OpenAI/GitHub/Slack/AWS credentials, private keys, and inline
  `GOOGLE_API_KEY` / `BROWSER_STATE_SECRET` values
- committed `.env` files

CI runs the same check plus the offline test suite.

## Secrets

Never commit API keys, SMTP passwords, or session secrets.

- Local development: use `scratchpad/.env` (gitignored) or export variables.
- Gemini: `GOOGLE_API_KEY`
- Gradio session state: `BROWSER_STATE_SECRET`
- Hugging Face Spaces: configure these as **Space secrets**, not repository files.
- Gmail SMTP/IMAP: users enter an App Password in the UI at runtime; it must not
  be written to disk or logs.

If a secret is accidentally committed, treat it as compromised:

1. Revoke/rotate it immediately.
2. Remove it from the working tree.
3. Purge it from Git history (see below).
4. Force-push the rewritten history only after coordinating with collaborators.

## Removing PII or secrets from Git history

Deleting a file in a new commit does **not** remove it from earlier commits.
Use `git filter-repo` in a fresh clone:

```bash
pip install git-filter-repo
git clone <repo-url> repo-clean
cd repo-clean
git filter-repo --replace-text replacements.txt
git push --force-with-lease origin main
```

`replacements.txt` maps each leaked value to a synthetic replacement, for
example:

```text
LEAKED_EMAIL==>old.email@example.com
LEAKED_PHONE==>+1 (212) 555-0100
```

Keep `replacements.txt` out of the repository. After rewriting, invalidate old
clones and rotate any exposed credentials.

## GitHub account privacy

Enable **Settings → Emails → Keep my email addresses private** and use GitHub's
`noreply` address for commits. This prevents a personal email from appearing in
commit metadata even when the working tree is clean.

## Prompt-injection and LLM safety

Resume, JD, and reply content is untrusted data, never instructions. The
quarantined extractor has no tools and receives PII-minimised text. Every
extracted skill, years claim, and education entry must cite an exact quote from
the source or it is dropped (`src/extractor.py`). Injection indicators and
hidden PDF text set `needs_human_review`; large LLM-vs-regex disagreement does
the same. Scoring remains deterministic (`src/scoring.py`).

All model-callable tools are allow-listed and role/capability checked in
`src/guardrails.py`; arguments containing CR/LF/NUL or invalid email addresses
are rejected. There is deliberately **no send-email tool**. Drafts enter
`src/approval.py`, are previewed by HR, and only an explicitly approved draft
can reach the deterministic mailer.

Per-run budgets (`MAX_LLM_CALLS`, `MAX_TOKENS`, `MAX_SECONDS`) stop model work
and degrade to deterministic mode rather than failing open. Trace spans are
bounded and PII-masked. Do not remove sanitisation, quote verification, approval
gates, or budget checks when adding tools or agents.

## Access control

`src/security.py` provides role-based access control:

| Role | Read resumes | View unmasked PII | Send invitations | Write reports |
|---|---|---|---|---|
| `admin` | yes | yes | yes | yes |
| `recruiter` | yes | yes | yes | yes |
| `hiring_manager` | yes | no | no | no |
| `viewer` | no | no | no | no |

Log records pass through `PiiMaskingFilter`; truncation uses `safe_preview()` so
a partial email cannot leak. Masking covers emails, phone numbers, URLs,
tracebacks, and local filesystem paths while preserving ordinary years and
non-phone numbers. Audit decisions are stored in `SecurityContext.audit_log`.
When a role lacks a permission, the affected stage is skipped and the denial is
recorded instead of crashing the pipeline.

### UI authentication

The Gradio app refuses to start unless `HR_USERS` is configured or
`HR_ALLOW_NO_AUTH=1` is set for local development. With authentication enabled,
the app binds to `0.0.0.0`; in no-auth mode it binds to `127.0.0.1` only.

`HR_USERS` accepts `username:role:pbkdf2_hash` entries separated by `;` (or JSON).
Generate a hash without writing the password to history:

```bash
python -m src.auth --hash-password 'your-password'
```

Logins are rate-limited by `HR_AUTH_MAX_FAILURES` and
`HR_AUTH_LOCKOUT_SECONDS`; audit events are written to `HR_AUTH_AUDIT_LOG` with
usernames and other PII masked. Passwords are never logged.

## Known limitations

- The live Gemini agents are optional. Without `GOOGLE_API_KEY`/`GEMINI_MODEL`,
  the deterministic orchestrator runs the same skills and is the CI-tested path.
- Hidden-PDF-text detection is a content-stream heuristic (pypdf), not an OCR or
  rendering comparison. Treat any hidden-text flag as a reason for human review.
- Approval is enforced in the application/mailer path. Operators who call the
  Brevo transport directly bypass that gate.
- Thresholds and interview timezone are environment-driven (`src/config.py`);
  changing them does not change the deterministic scoring formula.
- The UI's email features send real mail if configured with real credentials.
  Use test accounts and synthetic candidates when demonstrating the project.

## Reporting

If you find a vulnerability or exposed credential, do not open a public issue
with the value. Contact the maintainer privately and allow time for rotation and
history cleanup before disclosure.
