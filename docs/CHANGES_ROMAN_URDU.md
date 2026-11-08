# Changes — Phase 0 se Phase 5 (Roman Urdu)

Yeh document har major phase ka simple Roman Urdu summary hai: **kya tha**,
**kya kiya**, **kaise kaam karta hai**, aur ek **chhota example**. Har phase ne
system ko zyada safe, zyada explainable, ya zyada production-ready banaya.

---

## Phase 0 — Safe foundation aur synthetic data

### Kya tha
Shuru mein real candidate data ya real PII commit hone ka risk tha. Tests ke liye
bhi aisa data chahiye tha jo safely repository mein rakha ja sake.

### Kya kiya
- 18 **fully synthetic resumes** aur ek synthetic job description banaye.
- `tools/generate_synthetic_resumes.py` add kiya taake fixtures dobara generate
  ho saken.
- `tools/check_no_pii.py` banaya: emails sirf `@example.com` allowed, phone
  numbers mein `555` exchange zaroori, aur common secret formats block.
- `SECURITY.md` likha: data policy, secrets, aur agar galti se real data commit
  ho jaye to Git history purge + credentials rotate karne ki guidance.
- `.gitignore` mein `.env`, runtime output, databases, aur caches add kiye.

### Kaise kaam karta hai
PII gate poore repository ko scan karta hai. Agar koi non-example email, non-555
phone, ya API key jaisa pattern mile to check fail ho jata hai. CI bhi isi gate
ko chalata hai, is liye unsafe data merge nahi ho sakta.

### Chhota example
```bash
python tools/check_no_pii.py --all
# OK: no real PII or committed secrets found
```
Agar file mein kisi real domain wala email ho (jaise gmail.com) to check fail
karega; `someone@example.com` aur `+1 415 555 0103` pass honge.

---

## Phase 1 — Correctness aur security hardening

### Kya tha
Initial scoring aur parsing mein kuch correctness issues the:
education dates ko work experience samajh lena, skills ko substring ki tarah
match karna, JD ki responsibilities ko required skills mein ghusa dena, aur
thresholds alag-alag files mein hardcoded hona.

### Kya kiya
- **Experience years fix**: education dates ab work experience mein count nahi
  hoti. Overlapping year ranges merge hote hain aur full years count hote hain.
- **Skill matching fix**: word boundaries use hoti hain, taake `Java` aur
  `JavaScript` ko galti se same na samjha jaye. Ambiguous skills ke liye list ya
  programming context chahiye.
- **JD parsing fix**: required skills sirf explicit requirements section se
  aate hain; responsibilities aur nice-to-have alag rehte hain. `AWS/Azure/GCP`
  jaisa any-of group kisi ek provider se satisfy ho jata hai.
- **Ek config source**: thresholds `src/config.py` se aate hain aur environment
  se override ho sakte hain.
- **RBAC default-deny**: unknown capability deny hoti hai. `hiring_manager` role
  scheduling aur reporting skip karta hai aur PII masked rehti hai.
- **PII masking fix**: phone masking years ya plain numbers ko nahi khaati;
  profile URLs aur tracebacks bhi mask hote hain.
- **Timezone-aware slots**: interview slots configured IANA timezone mein bante
  hain.
- Unused dependencies aur superseded agent stubs hataaye.

### Kaise kaam karta hai
Scoring deterministic hai: pehle resume/JD parse hota hai, phir required skills,
experience, aur evidence ke against score banta hai. Har rule ka apna test hai,
is liye future changes purani galtiyan wapas nahi laate.

### Chhota example
Resume mein likha ho `B.Tech 2018-2022` aur `Software Engineer 2022-present` to
experience sirf 2022 se count hoga, 2018 se nahi. JD mein `AWS or Azure or GCP`
ho to candidate ke paas in mein se koi ek ho to requirement satisfy hogi.

---

## Phase 2 — Application security aur external-service limits

### Kya tha
App ko public deploy karna tha, lekin login, session, email scanning, aur
external APIs ke liye limits nahi the. Bina limits ke abuse ya resource
exhaustion ho sakta tha.

### Kya kiya
- `src/auth.py` mein **PBKDF2 password hashing**, login failure lockout, aur
  audit log add kiya.
- `HR_USERS` se users/roles configure hote hain; local development ke liye
  `HR_ALLOW_NO_AUTH=1` sirf `127.0.0.1` par allowed hai.
- IMAP scanning ke liye limits: message size, attachment size, text length, PDF
  pages, scan count.
- DOCX parsing ke liye uncompressed size, compression ratio, aur XML size limits.
- Brevo email sending ke liye timeout aur batch limit.
- Request queue size, concurrency limit, server threads, session state capacity,
  aur temp-file cleanup limits add kiye.
- `BROWSER_STATE_SECRET` optional kiya: agar set ho to Gradio BrowserState use
  hota hai, warna in-memory state.

### Kaise kaam karta hai
Har external call se pehle limits check hoti hain. Login failures count hote
hain; limit cross ho to account temporarily lock ho jata hai. Audit log mein
security-relevant actions likhe jate hain, lekin secrets aur raw PII nahi.

### Chhota example
Agar `HR_AUTH_MAX_FAILURES=5` aur `HR_AUTH_LOCKOUT_SECONDS=300` hain, to 5 galat
passwords ke baad 5 minute ka lockout hoga. IMAP scan sirf `IMAP_SCAN_LIMIT`
messages tak dekhega, poori inbox ek hi baar mein load nahi hogi.

---

## Phase 3 — Real agentic pipeline (ADK coordinator)

### Kya tha
Pehle pipeline mostly deterministic tha. Goal tha ADK agents use karna, lekin
model ko send/save/browse jaisi dangerous powers na dena.

### Kya kiya
- `src/coordinator.py` mein ADK coordinator banaya jo JD analyst, quarantined
  extractor, parallel screeners, critic loop, draft-only email writer, aur reply
  triage ko coordinate karta hai.
- **Quarantined extractor** (`src/extractor.py`) ko no-tools banaya: sirf ek
  structured extraction call, koi filesystem/network/mail tool nahi.
- **Quote verification**: extractor ka har claim resume ki exact quote se match
  hona chahiye; unverified claims drop ho jate hain.
- **Parallel screeners** aur **critic loop** (max 2 iterations) add kiye.
- **Draft-only email writer**: email schema mein send capability hi nahi.
- **Reply triage**: candidate reply ka intent + confidence nikalta hai; low
  confidence par human review.
- **Per-run budgets** (`src/budget.py`): LLM calls, tokens, steps, aur seconds.
  Limit cross ho to deterministic mode mein graceful degradation.
- **HMAC-keyed memory** (`src/memory.py`): sirf `HMAC(email)` store hota hai,
  raw email nahi. Memory advisory hai, score change nahi karti.
- **Bounded tracing** (`src/tracing.py`) aur **approval queue**
  (`src/approval.py`) add kiye.
- MCP filesystem server se sandboxed file tools diye; path traversal blocked.
- Koi bhi model tool email send nahi kar sakta.

### Kaise kaam karta hai
Coordinator untrusted resume text ko sirf extractor ke paas bhejta hai. Extractor
claims + quotes deta hai; verification ke baad deterministic scorer final score
banata hai. Drafts approval queue mein jate hain; human approve kare tab hi
deterministic mailer bhejta hai. Model fail ho ya budget khatam ho to system
regex/deterministic path par chala jata hai aur UI par banner dikhata hai.

### Chhota example
Resume mein `Python` likha ho lekin experience section mein Python ka koi quote
na mile, to extractor ka Python claim drop ho jayega. Agar `MAX_LLM_CALLS=24` ho
aur 24 calls ho jayen, to agla model call block hoga aur result deterministic
mode mein milta rahega.

---

## Phase 4 — Evaluation aur fairness

### Kya tha
System ko sirf “chal raha hai” nahi, balki “sahi aur fair hai” prove karna tha.
Iske liye repeatable evaluation sets chahiye the.

### Kya kiya
- `tests/eval/golden_set.json`: expected score bands ke saath known resumes.
- `tests/eval/injection_set.json`: prompt-injection attacks ke tests.
- `tests/eval/reply_labels.json`: reply triage labels, including **Roman Urdu**
  aur **Urdu** decline phrases.
- `tests/eval/adverse_impact.json` aur `src/fairness.py`: **four-fifths (80%)
  rule** ke saath adverse-impact report.
- **Blind mode** (`src/fairness.py`): scoring se pehle name aur direct contact
  details mask hote hain.
- `tests/eval/adk_evalset.json` aur `adk_eval_config.json`: ADK trajectory/tool
  use evaluation.
- Tests add kiye jo golden scores, injection safety, reply labels, blind scoring,
  aur fairness report verify karte hain.

### Kaise kaam karta hai
Golden set batata hai ke correct candidate kis score band mein aana chahiye.
Injection set verify karta hai ke malicious text score ya tool calls change na
kare. Blind mode mein name badalne se score same rehna chahiye. Fairness report
har synthetic group ka selection rate nikalta hai; agar koi group highest rate
ke 80% se neeche ho to flag hota hai.

### Chhota example
Ek candidate ka naam `A` se `B` kar dene par bhi `match_percent` same rahe, to
blind scoring sahi hai. Agar group X ka selection rate 50% aur group Y ka 80% ho,
to X ka ratio 62.5% hai jo 80% se kam hai, is liye report flag karegi.

---

## Phase 5 — Explainability, workspaces, exports, calendar, deployment, Gmail App Password

### Kya tha
Recruiters ko score ka reason, multiple jobs, export, scheduling, aur safe email
access chahiye tha. Pehle Gmail ke liye OAuth flow tha, jo deploy karna mushkil
tha; woh revert kar diya gaya.

### Kya kiya
- **Score cards** (`src/scorecard.py`): har candidate ke verified evidence quotes
  aur “why not shortlisted” ke actual blockers dikhate hain.
- **CSV export** (`src/exporters.py`): stable columns, UTF-8 BOM, aur formula
  injection guard. `=`, `+`, `-`, `@` se shuru hone wale cells ko quote kiya
  jata hai.
- **Multi-job workspace** (`src/workspace.py`): job configuration JSON mein
  save/switch hoti hai; candidate data ya credentials store nahi hote.
- **Calendar free/busy + .ics** (`src/calendar_api.py`): configured calendar se
  free slots; warna built-in business-hour slots. Invitation ke saath standard
  `.ics` file attach hoti hai.
- **Deployment**: `Dockerfile`, `render.yaml`, aur ruff lint gate add kiye.
- **Gmail App Password flow**: Gmail inbox **IMAP** se padhi jati hai. User UI
  mein apna Gmail address aur 16-character **App Password** enter karta hai.
  Credentials browser session mein rehte hain aur disk par save nahi hote.
- **Email sending**: invitations **Brevo** transactional email API se jati hain;
  verified sender email aur Brevo API key UI se aate hain.
- README update kiya: OAuth references hata kar App Password flow document kiya.

### Kaise kaam karta hai
Applications tab Gmail inbox ko IMAP + App Password se scan karta hai, resume
attach karke pipeline mein daalta hai. Ranked candidates se invitation drafts
bante hain, human approve karta hai, phir Brevo se email jata hai. Replies tab
usi IMAP connection se sirf invitation replies dhoondta hai. Score cards aur CSV
export deterministic results se bante hain, is liye model ki marzi se nahi
badalte.

### Chhota example
CSV cell mein agar `=SUM(A1:A2)` likha ho to export usay `'=SUM(A1:A2)` bana
deta hai, taake spreadsheet formula execute na ho. Gmail ke liye UI mein
`you@example.com` aur App Password daalne se Applications/Replies tabs kaam
karte hain; normal Gmail password kabhi use nahi hota.

---

## Ek nazar mein

| Phase | Main kaam | Faaida |
|---|---|---|
| 0 | Synthetic data + PII gate + security docs | Safe repository, no real PII |
| 1 | Scoring/parsing correctness + RBAC/PII fixes | Reliable aur predictable results |
| 2 | Auth, lockout, audit, external-service limits | Public deployment ke liye safety |
| 3 | ADK coordinator, quarantine, budgets, approval | Real agents, no send power |
| 4 | Golden/injection/fairness evaluation | Quality aur fairness proof |
| 5 | Score cards, workspace, CSV, calendar, App Password | Explainable aur usable product |
