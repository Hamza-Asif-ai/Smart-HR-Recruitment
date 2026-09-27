"""
app.py
======
Polished Gradio UI for the Smart HR Recruitment multi-agent system.

Run locally:
    python app.py

Deploy on Hugging Face Spaces:
    1. Create a new Space -> SDK: Gradio
    2. Push this repo (app.py must be at the repo root)
    3. Space auto-installs from requirements.txt and launches app.py

Email sending/receiving:
    - Uses Gmail SMTP (send) and IMAP (read replies) with an App Password.
    - Credentials are entered in the UI at runtime and are NEVER written to disk.
"""

from __future__ import annotations

import os
import html as _html
import imaplib
import email as email_lib
import email.utils
import smtplib
import requests
from io import BytesIO
from collections import Counter
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pypdf
from src import skills as skills_mod
from src import mcp_filesystem_server as fs_mod

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import gradio as gr

from src.orchestrator import run_pipeline

os.environ.setdefault("HR_DATA_ROOT", "./data")

# --------------------------------------------------------------------------- #
# Theme & styling
# --------------------------------------------------------------------------- #
THEME = gr.themes.Soft(
    primary_hue="indigo",
    secondary_hue="violet",
    neutral_hue="slate",
    font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui",
          "Segoe UI Emoji", "Noto Color Emoji", "Apple Color Emoji"],
).set(
    body_background_fill="#0b0b12",
    body_background_fill_dark="#0b0b12",
    block_background_fill="#14141f",
    block_background_fill_dark="#14141f",
    block_border_width="1px",
    block_border_color="#232336",
    block_radius="18px",
    block_shadow="0 10px 34px rgba(0,0,0,0.45)",
    button_primary_background_fill="linear-gradient(90deg, #6366f1, #a855f7)",
    button_primary_background_fill_hover="linear-gradient(90deg, #4f46e5, #9333ea)",
    button_primary_text_color="white",
    body_text_color="#e4e4f0",
    body_text_color_subdued="#8b8ba7",
    input_background_fill="#1a1a29",
    input_border_color="#2a2a3d",
)

CSS = """
.gradio-container {max-width: 1560px !important; margin: 0 auto !important;}

/* ---------- Header ---------- */
#header-wrap {display:flex; align-items:center; justify-content:center; gap:14px;
              padding: 18px 0 6px 0;}
#logo-badge {width:46px; height:46px; border-radius:13px; flex-shrink:0;
             background: linear-gradient(140deg,#6366f1,#c084fc);
             display:flex; align-items:center; justify-content:center;
             font-weight:800; color:white; font-size:1.05rem; letter-spacing:-0.5px;
             box-shadow: 0 6px 18px rgba(139,92,246,0.45);}
#header-text h1 {font-size: 1.85rem; font-weight: 800; margin:0; line-height:1.15;
            background: linear-gradient(90deg,#a5b4fc,#e9d5ff);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;}
#header-text p {color: #8b8ba7; font-size: 0.88rem; margin:2px 0 0 0;}
#badges {display:flex; gap:8px; justify-content:center; margin: 10px 0 4px 0; flex-wrap:wrap;}
.pill {font-size:0.72rem; padding:4px 11px; border-radius:999px; color:#c4b5fd;
       background: rgba(139,92,246,0.12); border:1px solid rgba(139,92,246,0.3);
       letter-spacing:.02em;}

/* ---------- Stat cards ---------- */
.stat-card {border-radius: 16px; padding: 20px 14px; text-align:center;
            border: 1px solid #26263a;
            background: linear-gradient(165deg, rgba(99,102,241,0.14), rgba(168,85,247,0.04));}
.stat-card .num {font-size: 2rem; font-weight: 800; color: #ede9fe; line-height:1;}
.stat-card .lbl {font-size: 0.72rem; color: #9694b5; margin-top: 6px;
                  letter-spacing:.08em; text-transform:uppercase;}

/* ---------- Tabs ---------- */
.tabs .tab-nav {border-bottom: 1px solid #232336 !important; gap: 6px;}
.tabs .tab-nav button {border-radius: 10px 10px 0 0 !important; font-weight:600 !important;
                        color:#9694b5 !important; border:none !important;
                        padding: 10px 18px !important; background:transparent !important;}
.tabs .tab-nav button.selected {color:#e9d5ff !important;
                        background: linear-gradient(180deg, rgba(139,92,246,0.18), transparent) !important;
                        box-shadow: inset 0 -2px 0 #a855f7 !important;}

/* ---------- Candidate table ---------- */
.cand-table-wrap {max-height: 560px; overflow-y: auto; border-radius:14px;
                   border:1px solid #232336;}
table.cand-table {width:100%; border-collapse: collapse; font-size:0.86rem;}
table.cand-table thead th {position:sticky; top:0; background:#181826; color:#9694b5;
                            text-align:left; padding:11px 14px; font-weight:600;
                            font-size:0.72rem; letter-spacing:.06em; text-transform:uppercase;
                            border-bottom:1px solid #2a2a3d; z-index:1;}
table.cand-table td {padding:11px 14px; border-bottom:1px solid #1d1d2c; color:#dcdce8;
                      vertical-align:middle;}
table.cand-table tr:hover td {background:#181826;}
.rank-badge {display:inline-flex; align-items:center; justify-content:center;
             width:26px; height:26px; border-radius:8px; font-weight:700; font-size:0.78rem;
             background:#232336; color:#c4b5fd;}
.rank-badge.top {background: linear-gradient(140deg,#6366f1,#c084fc); color:white;}
.cand-name {font-weight:600; color:#f1f1f8;}
.cand-email {color:#8b8ba7; font-size:0.78rem;}
.match-cell {display:flex; align-items:center; gap:8px; min-width:120px;}
.match-bar-bg {flex:1; height:6px; border-radius:4px; background:#232336; overflow:hidden;}
.match-bar-fill {height:100%; border-radius:4px; background: linear-gradient(90deg,#6366f1,#c084fc);}
.match-pct {font-weight:700; font-size:0.8rem; color:#e9d5ff; width:38px; text-align:right;}
.chip {display:inline-block; padding:4px 10px; border-radius:999px; font-size:0.72rem; font-weight:600;}
.chip.shortlist {background:rgba(34,197,94,0.14); color:#4ade80; border:1px solid rgba(74,222,128,0.3);}
.chip.review {background:rgba(245,158,11,0.14); color:#fbbf24; border:1px solid rgba(251,191,36,0.3);}
.chip.reject {background:rgba(244,63,94,0.12); color:#fb7185; border:1px solid rgba(251,113,133,0.3);}

/* ---------- Email cards ---------- */
.email-card {border:1px solid #232336; border-radius:14px; padding:16px 18px; margin-bottom:12px;
             background:#12121c;}
.email-top {display:flex; align-items:center; gap:12px; margin-bottom:10px;}
.email-avatar {width:36px; height:36px; border-radius:50%; flex-shrink:0;
                background: linear-gradient(140deg,#6366f1,#c084fc); color:white;
                display:flex; align-items:center; justify-content:center; font-weight:700; font-size:0.85rem;}
.email-to {font-weight:600; color:#f1f1f8; font-size:0.88rem;}
.email-addr {color:#8b8ba7; font-size:0.76rem;}
.email-subject {color:#c4b5fd; font-weight:600; font-size:0.85rem; margin: 8px 0 8px 0;}
.email-body {color:#b7b7cc; font-size:0.84rem; line-height:1.6; white-space:pre-wrap;
             border-left: 2px solid #2a2a3d; padding-left:12px;}
.empty-state {text-align:center; color:#8b8ba7; padding: 40px 0; font-size:0.9rem;}

/* ---------- Status log ---------- */
.status-line {padding:8px 12px; border-radius:8px; font-size:0.82rem; margin-bottom:6px;
              font-family: ui-monospace, monospace;}
.status-line.ok {background:rgba(34,197,94,0.1); color:#4ade80;}
.status-line.err {background:rgba(244,63,94,0.1); color:#fb7185;}
.status-line.info {background:rgba(99,102,241,0.1); color:#a5b4fc;}

/* ---------- Reply cards ---------- */
.reply-card {border:1px solid #232336; border-radius:14px; padding:16px 18px;
             background:#12121c; border-left:3px solid #4ade80;}
.reply-date {color:#8b8ba7; font-size:0.74rem; float:right;}
.reply-grid {display:grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
             gap:14px; align-items:start;}
.attach-chip {display:inline-block; padding:3px 9px; border-radius:999px; font-size:0.7rem;
              font-weight:600; background:rgba(99,102,241,0.14); color:#a5b4fc;
              border:1px solid rgba(99,102,241,0.3); margin-left:6px;}
.skills-row {display:flex; flex-wrap:wrap; gap:5px; max-width:260px;}
.skill-chip {font-size:0.68rem; padding:2px 8px; border-radius:999px;
             background:rgba(139,92,246,0.12); color:#c4b5fd; border:1px solid rgba(139,92,246,0.25);}
.skill-chip.missing {background:rgba(244,63,94,0.08); color:#fca5a5; border-color:rgba(251,113,133,0.2);}

footer {display:none !important;}

/* ---------- Dropdown options popup ---------- */
ul.options, div[role="listbox"] {
  background: #14141f !important;
  border: 1px solid #2a2a3d !important;
}
ul.options li, div[role="listbox"] div[role="option"], li.item {
  color: #e4e4f0 !important;
  background: transparent !important;
}
ul.options li:hover, div[role="listbox"] div[role="option"]:hover, li.item:hover,
ul.options li.selected, li.item.selected {
  background: #2a2a45 !important;
  color: #e9d5ff !important;
}
"""


def stat_card(number, label):
    return f'<div class="stat-card"><div class="num">{number}</div><div class="lbl">{label}</div></div>'


REC_CHIP = {
    "Strong match - shortlist": ("shortlist", "Shortlist"),
    "Possible match - review": ("review", "Review"),
    "Weak match - likely reject": ("reject", "Reject"),
}


def render_candidate_table(ranked):
    rows_html = []
    for c in ranked:
        cls, label = REC_CHIP.get(c["recommendation"], ("review", c["recommendation"]))
        rank_cls = "rank-badge top" if c["rank"] <= 3 else "rank-badge"
        name = _html.escape(c["name"])
        email_addr = _html.escape(c["email"])
        pct = c["match_percent"]

        matched = c.get("matched_required", []) or []
        skill_chips = "".join(f'<span class="skill-chip">{_html.escape(s)}</span>' for s in matched[:5])
        more = len(matched) - 5
        if more > 0:
            skill_chips += f'<span class="skill-chip">+{more} more</span>'
        if not matched:
            skill_chips = '<span class="skill-chip missing">No required skills matched</span>'

        rows_html.append(f"""
        <tr>
          <td><span class="{rank_cls}">{c['rank']}</span></td>
          <td><div class="cand-name">{name}</div><div class="cand-email">{email_addr}</div></td>
          <td>
            <div class="match-cell">
              <div class="match-bar-bg"><div class="match-bar-fill" style="width:{pct}%;"></div></div>
              <div class="match-pct">{pct}%</div>
            </div>
          </td>
          <td><div class="skills-row">{skill_chips}</div></td>
          <td><span class="chip {cls}">{label}</span></td>
        </tr>""")
    if not rows_html:
        return '<div class="empty-state">No candidates screened yet — click Run Pipeline.</div>'
    return f"""
    <div class="cand-table-wrap">
    <table class="cand-table">
      <thead><tr><th>#</th><th>Candidate</th><th>Match Score</th><th>Why shortlisted (matched skills)</th><th>Recommendation</th></tr></thead>
      <tbody>{''.join(rows_html)}</tbody>
    </table>
    </div>"""


def _extract_first_name(body: str, fallback: str) -> str:
    first_line = body.split("\n", 1)[0].strip()
    if first_line.lower().startswith("dear "):
        return first_line[5:].rstrip(",").strip() or fallback
    return fallback


def render_emails(emails):
    if not emails:
        return '<div class="empty-state">No candidates crossed the shortlist threshold this run.</div>'
    cards = []
    for e in emails:
        fallback = e["to"].split("@")[0].replace(".", " ").title()
        name = _extract_first_name(e.get("body", ""), fallback)
        initials = "".join([p[0].upper() for p in name.split()[:2]]) or "?"
        subject = _html.escape(e["subject"])
        body = _html.escape(e["body"])
        to_addr = _html.escape(e["to"])
        cards.append(f"""
        <div class="email-card">
          <div class="email-top">
            <div class="email-avatar">{initials}</div>
            <div>
              <div class="email-to">{_html.escape(name)}</div>
              <div class="email-addr">{to_addr}</div>
            </div>
          </div>
          <div class="email-subject">✉ {subject}</div>
          <div class="email-body">{body}</div>
        </div>""")
    return "".join(cards)


def _disambiguated_labels(names_full):
    firsts = [n.split()[0] for n in names_full]
    counts = Counter(firsts)
    labels = []
    for n in names_full:
        parts = n.replace('"', "").split()
        first = parts[0]
        if counts[first] > 1 and len(parts) > 1:
            labels.append(f"{first} {parts[-1][0]}.")
        else:
            labels.append(first)
    return labels


def make_score_chart(ranked):
    n = max(len(ranked), 1)
    names = _disambiguated_labels([c["name"] for c in ranked])
    scores = [c["match_percent"] for c in ranked]
    colors = ["#a855f7" if s >= 70 else "#3f3f52" for s in scores]

    fig, ax = plt.subplots(figsize=(7.6, max(3.6, n * 0.34)), facecolor="#12121c")
    ax.set_facecolor("#12121c")
    bars = ax.barh(names[::-1], scores[::-1], color=colors[::-1], height=0.58)
    ax.axvline(70, color="#f472b6", linestyle="--", linewidth=1, alpha=0.7)
    ax.text(71, n - 0.5, "70% shortlist line", color="#f472b6", fontsize=8)
    ax.set_xlim(0, 108)
    ax.set_xlabel("Match %", color="#c9c9dd", fontsize=9)
    ax.tick_params(colors="#c9c9dd", labelsize=8.5)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(axis="x", color="#232336", linewidth=0.6)
    ax.set_axisbelow(True)
    for bar, score in zip(bars, scores[::-1]):
        ax.text(bar.get_width() + 1.5, bar.get_y() + bar.get_height() / 2,
                 f"{score:.0f}%", va="center", color="#e9e9f5", fontsize=8)
    fig.tight_layout()
    return fig


def make_skills_chart(top_skills):
    if not top_skills:
        fig, ax = plt.subplots(figsize=(6, 4), facecolor="#12121c")
        ax.set_facecolor("#12121c")
        ax.text(0.5, 0.5, "No skill data", ha="center", color="#8b8ba7")
        ax.axis("off")
        return fig
    labels, counts = zip(*top_skills)
    fig, ax = plt.subplots(figsize=(7.2, 4.4), facecolor="#12121c")
    ax.set_facecolor("#12121c")
    bars = ax.bar(labels, counts, color="#6366f1", width=0.6)
    ax.tick_params(colors="#c9c9dd", labelsize=8.5, rotation=25)
    ax.set_ylabel("Candidates", color="#c9c9dd", fontsize=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.grid(axis="y", color="#232336", linewidth=0.6)
    ax.set_axisbelow(True)
    for bar, c in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.15,
                 str(c), ha="center", color="#e9e9f5", fontsize=8.5)
    fig.tight_layout()
    return fig


def execute_pipeline(role):
    result = run_pipeline(role=role or "recruiter")
    ranked = result["ranked_candidates"]
    emails = result["interview_emails"]
    insights = result["insights"]

    stats_html = (
        '<div style="display:grid; grid-template-columns:repeat(4,1fr); gap:14px;">'
        + stat_card(insights["total_applicants"], "Applicants")
        + stat_card(insights["shortlisted"], "Shortlisted")
        + stat_card(f'{insights["shortlist_rate_percent"]}%', "Shortlist Rate")
        + stat_card(f'{insights["average_match_percent"]}%', "Avg. Match")
        + "</div>"
    )

    table_html = render_candidate_table(ranked)
    score_fig = make_score_chart(ranked)
    skills_fig = make_skills_chart(insights["top_skills_found"])
    emails_html = render_emails(emails)

    return stats_html, table_html, score_fig, skills_fig, emails_html, emails


# --------------------------------------------------------------------------- #
# Email sending (SMTP) & replies (IMAP) — credentials come from the UI only
# --------------------------------------------------------------------------- #
def send_all_emails(sender_email, sender_name, brevo_api_key, emails_state):
    """
    Sends interview emails via the Brevo transactional email HTTP API.
    Uses HTTPS (port 443) so it works from any host, including free-tier PaaS
    platforms (Render, etc.) that block outbound SMTP ports 25/465/587.
    Unlike Resend's sandbox domain, Brevo lets you verify a single sender EMAIL
    (no domain purchase needed) and send to any recipient after that.
    """
    if not sender_email or not brevo_api_key:
        return '<div class="status-line err">⚠ Please enter both the sender email and the Brevo API key.</div>'
    if not emails_state:
        return '<div class="status-line err">⚠ Run the pipeline first to generate the interview emails.</div>'

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": brevo_api_key,
        "content-type": "application/json",
    }
    lines = []
    for e in emails_state:
        payload = {
            "sender": {"name": sender_name or "HR Team", "email": sender_email},
            "to": [{"email": e["to"]}],
            "subject": e["subject"],
            "textContent": e["body"],
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if resp.status_code in (200, 201):
                lines.append(f'<div class="status-line ok">✅ Sent to {_html.escape(e["to"])}</div>')
            else:
                try:
                    detail = resp.json().get("message", resp.text)
                except Exception:
                    detail = resp.text
                lines.append(
                    f'<div class="status-line err">❌ {_html.escape(e["to"])} — '
                    f'{resp.status_code}: {_html.escape(str(detail))}</div>'
                )
        except Exception as ex:
            lines.append(f'<div class="status-line err">❌ {_html.escape(e["to"])} — {_html.escape(str(ex))}</div>')

    lines.append(f'<div class="status-line info">📨 {len(emails_state)} email(s) processed.</div>')
    return "".join(lines)


def _extract_plain_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                try:
                    return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="ignore")
    except Exception:
        return ""


def check_replies(sender_email, app_password, emails_state):
    if not sender_email or not app_password:
        return '<div class="status-line err">⚠ Please enter both the Gmail address and the App Password.</div>'
    if not emails_state:
        return '<div class="status-line err">⚠ Run the pipeline first — that\'s how the candidate email list is known.</div>'

    candidate_addrs = {e["to"].lower() for e in emails_state}
    replies = []
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(sender_email, app_password)
        imap.select("INBOX")
        status, data = imap.search(None, "ALL")
        ids = data[0].split()[-100:]  # last 100 messages, newest last
        for msg_id in reversed(ids):
            status, msg_data = imap.fetch(msg_id, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            from_addr = email.utils.parseaddr(msg.get("From", ""))[1].lower()
            if from_addr in candidate_addrs:
                body = _extract_plain_body(msg).strip()
                replies.append({
                    "from": from_addr,
                    "subject": msg.get("Subject", "(no subject)"),
                    "date": msg.get("Date", ""),
                    "body": body[:600],
                })
        imap.logout()
    except imaplib.IMAP4.error as ex:
        return f'<div class="status-line err">❌ IMAP error: {_html.escape(str(ex))} (check the App Password)</div>'
    except Exception as ex:
        return f'<div class="status-line err">❌ Error: {_html.escape(str(ex))}</div>'

    if not replies:
        return '<div class="empty-state">No replies from shortlisted candidates found in the inbox yet.</div>'

    cards = []
    for r in replies:
        cards.append(f"""
        <div class="reply-card">
          <span class="reply-date">{_html.escape(r['date'])}</span>
          <div class="email-to">{_html.escape(r['from'])}</div>
          <div class="email-subject">✉ {_html.escape(r['subject'])}</div>
          <div class="email-body">{_html.escape(r['body'])}</div>
        </div>""")
    return f'<div class="reply-grid">{"".join(cards)}</div>'


_APPLICATION_KEYWORDS = [
    "resume", "cv", "curriculum vitae", "application", "applying", "apply for",
    "job application", "position", "vacancy", "candidate", "cover letter",
]


def _find_attachment_name(msg):
    if not msg.is_multipart():
        return None
    for part in msg.walk():
        cd = part.get("Content-Disposition", "") or ""
        if "attachment" in cd.lower():
            filename = part.get_filename() or ""
            if filename.lower().endswith((".pdf", ".doc", ".docx", ".rtf", ".txt")):
                return filename
    return None


def _extract_attachment_text(msg):
    """Return (filename, extracted_text) for the first resume-like attachment, or (None, "")."""
    if not msg.is_multipart():
        return None, ""
    for part in msg.walk():
        cd = part.get("Content-Disposition", "") or ""
        if "attachment" not in cd.lower():
            continue
        filename = part.get_filename() or ""
        low = filename.lower()
        if not low.endswith((".pdf", ".txt", ".rtf")):
            # .doc/.docx text extraction needs extra libraries we don't depend on;
            # skip those and fall back to the email body instead.
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        if low.endswith(".pdf"):
            try:
                reader = pypdf.PdfReader(BytesIO(payload))
                text = "\n".join((page.extract_text() or "") for page in reader.pages)
                return filename, text
            except Exception:
                continue
        else:
            try:
                return filename, payload.decode("utf-8", errors="ignore")
            except Exception:
                continue
    return None, ""


def analyze_inbox_applications(gmail_address, app_password, limit=60):
    """
    Scans the given Gmail inbox for candidate application emails, extracts
    each one's resume (attachment text when available, else the email body),
    and scores it against the current job description using the same
    resume_parser_skill / candidate_scorer_skill used by the local pipeline.
    Returns candidates ranked exactly like the file-based pipeline, so the
    Ranked Candidates tab and Dashboard reflect real inbox applicants.
    """
    empty_state = {"title": "", "candidates": []}
    if not gmail_address or not app_password:
        msg = '<div class="status-line err">⚠ Please enter both the Gmail address and the App Password.</div>'
        return msg, render_candidate_table([]), make_score_chart([]), make_skills_chart([]), gr.update(choices=[], value=[]), empty_state

    try:
        jd_text = fs_mod.read_job_description()
        requirements = skills_mod.jd_parser_skill(jd_text)
    except Exception as ex:
        msg = f'<div class="status-line err">❌ Could not read the job description: {_html.escape(str(ex))}</div>'
        return msg, render_candidate_table([]), make_score_chart([]), make_skills_chart([]), gr.update(choices=[], value=[]), empty_state

    candidates = []
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(gmail_address, app_password)
        imap.select("INBOX")
        status, data = imap.search(None, "ALL")
        ids = data[0].split()[-limit:]
        for msg_id in reversed(ids):
            status, msg_data = imap.fetch(msg_id, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            subject = msg.get("Subject", "") or ""
            body = _extract_plain_body(msg)
            attachment_name, attachment_text = _extract_attachment_text(msg)
            blob = f"{subject} {body}".lower()
            has_attachment = _find_attachment_name(msg) is not None
            if not (has_attachment or any(k in blob for k in _APPLICATION_KEYWORDS)):
                continue

            from_name, from_addr = email.utils.parseaddr(msg.get("From", ""))
            resume_text = attachment_text.strip() if attachment_text.strip() else body

            profile = skills_mod.resume_parser_skill(resume_text, source_id=from_addr or subject)
            if from_name:
                profile["name"] = from_name
            elif not profile.get("name") or profile["name"] == "Unknown Candidate":
                profile["name"] = (from_addr.split("@")[0] if from_addr else "Unknown Candidate").title()
            if from_addr:
                profile["email"] = from_addr

            result = skills_mod.candidate_scorer_skill(profile, requirements)
            result["email"] = profile["email"]
            result["skills"] = profile["skills"]
            result["name"] = profile["name"]
            result["_subject"] = subject
            result["_attachment"] = attachment_name
            candidates.append(result)
        imap.logout()
    except imaplib.IMAP4.error as ex:
        msg = f'<div class="status-line err">❌ IMAP error: {_html.escape(str(ex))} (check the App Password)</div>'
        return msg, render_candidate_table([]), make_score_chart([]), make_skills_chart([]), gr.update(choices=[], value=[]), empty_state
    except Exception as ex:
        msg = f'<div class="status-line err">❌ Error: {_html.escape(str(ex))}</div>'
        return msg, render_candidate_table([]), make_score_chart([]), make_skills_chart([]), gr.update(choices=[], value=[]), empty_state

    candidates.sort(key=lambda c: c["match_percent"], reverse=True)
    for i, c in enumerate(candidates, 1):
        c["rank"] = i

    if not candidates:
        msg = '<div class="empty-state">No candidate application emails detected in this inbox.</div>'
        return msg, render_candidate_table([]), make_score_chart([]), make_skills_chart([]), gr.update(choices=[], value=[]), empty_state

    skill_counter = Counter()
    for c in candidates:
        skill_counter.update(c.get("skills", []))
    top_skills = skill_counter.most_common(8)

    table_html = render_candidate_table(candidates)
    score_fig = make_score_chart(candidates)
    skills_fig = make_skills_chart(top_skills)
    choice_tuples = [
        (f"#{c['rank']} {c['name']} — {c['match_percent']}% — {c['email']}", c["email"])
        for c in candidates
    ]
    status = (f'<div class="status-line ok">✅ Found {len(candidates)} candidate application email(s), '
              f'scored against "{_html.escape(requirements.get("title", ""))}".</div>')
    state = {"title": requirements.get("title", "the role"), "candidates": candidates}
    return status, table_html, score_fig, skills_fig, gr.update(choices=choice_tuples, value=[]), state


def send_invitations_to_selected(sender_email, sender_name, brevo_api_key, selected_emails, inbox_state):
    if not selected_emails:
        return '<div class="status-line err">⚠ Select at least one candidate first (checkboxes above).</div>'
    candidates = (inbox_state or {}).get("candidates", [])
    title = (inbox_state or {}).get("title") or "the role"
    by_email = {c["email"]: c for c in candidates}

    slots = skills_mod.suggest_interview_slots(len(selected_emails))
    drafts = []
    for i, addr in enumerate(selected_emails):
        c = by_email.get(addr)
        if not c:
            continue
        slot = slots[i] if i < len(slots) else ""
        drafts.append(skills_mod.email_drafter_skill(c, role_title=title, slot=slot))

    if not drafts:
        return '<div class="status-line err">⚠ Could not match the selected candidates — try scanning the inbox again.</div>'

    return send_all_emails(sender_email, sender_name, brevo_api_key, drafts)


def fetch_candidate_applications(gmail_address, app_password, limit=60):
    """Deprecated: superseded by analyze_inbox_applications, kept only if imported elsewhere."""
    return analyze_inbox_applications(gmail_address, app_password, limit)[0]


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
with gr.Blocks(theme=THEME, css=CSS, title="Smart HR Recruitment") as demo:
    emails_state = gr.State([])
    inbox_state = gr.State({"title": "", "candidates": []})

    gr.HTML("""
      <div id="header-wrap">
        <div id="logo-badge">SH</div>
        <div id="header-text">
          <h1>Smart HR Recruitment</h1>
          <p>Multi-agent AI pipeline — JD parsing → Resume screening → Interview scheduling → Insights</p>
        </div>
      </div>
      <div id="badges">
        <span class="pill">🔒 RBAC secured</span>
        <span class="pill">🧩 4 specialized agents</span>
        <span class="pill">📁 MCP filesystem</span>
      </div>
    """)

    with gr.Row():
        role_dd = gr.Dropdown(
            choices=[("Recruiter", "recruiter"), ("Hiring Manager", "hiring_manager"), ("Admin", "admin")],
            value="recruiter",
            label="Run as role",
            scale=2,
        )
        run_btn = gr.Button("▶  Run Pipeline (sample resumes)", variant="primary", scale=1)

    with gr.Accordion("📧 Gmail inbox access — enter YOUR OWN Gmail (used for Applications + Replies tabs, never saved)", open=False):
        gr.Markdown(
            "Any HR using this tool enters their own Gmail here at runtime — nothing is stored. "
            "Needs a **Gmail App Password** (not your normal password): generate one at "
            "`myaccount.google.com/apppasswords` (requires 2-Step Verification to be ON)."
        )
        with gr.Row():
            gmail_email_in = gr.Textbox(label="Gmail address", placeholder="you@gmail.com")
            gmail_app_password_in = gr.Textbox(label="App Password", type="password", placeholder="xxxx xxxx xxxx xxxx")

    with gr.Accordion("📧 Brevo sender details — used to actually send emails (never saved)", open=False):
        gr.Markdown(
            "Sends via the **Brevo** transactional email API (HTTPS) — works from the deployed "
            "public link too, unlike raw Gmail SMTP which most free hosts block.\n\n"
            "1. In your Brevo dashboard, go to **Senders & IP** (under Settings) and verify "
            "the email address you'll send from — a confirmation link is emailed to it\n"
            "2. Get your key under **Transactional → Settings → API Keys → Generate a new API key**"
        )
        with gr.Row():
            sender_email_in = gr.Textbox(label="Sender email (verified in Brevo)", placeholder="you@example.com")
            sender_name_in = gr.Textbox(label="Sender name", placeholder="HR Team", value="HR Team")
        brevo_key_in = gr.Textbox(label="Brevo API Key", type="password", placeholder="xkeysib-...")

    stats_out = gr.HTML()

    with gr.Tabs():
        with gr.Tab("📨  Applications Inbox"):
            gr.Markdown(
                "Scans the Gmail inbox above for emails that look like **candidate job applications** "
                "(resume attachments, or subject/body mentioning application-related keywords), scores "
                "each one against the job description, and feeds the results into the tabs on the right."
            )
            fetch_apps_btn = gr.Button("🔍  Scan Inbox for Candidate Applications", variant="primary")
            applications_status = gr.HTML()
            gr.Markdown("**Select candidates to invite for an interview:**")
            candidate_checkbox = gr.CheckboxGroup(choices=[], label="Detected candidates")
            send_selected_btn = gr.Button("📤  Send Interview Invitation to Selected", variant="primary")
            send_selected_status = gr.HTML()

        with gr.Tab("🏆  Ranked Candidates"):
            table_out = gr.HTML()

        with gr.Tab("📊  Dashboard"):
            with gr.Row():
                score_plot = gr.Plot(label="Candidate Match Scores")
                skills_plot = gr.Plot(label="Top Skills in Applicant Pool")

        with gr.Tab("✉️  Interview Emails"):
            gr.Markdown(
                "Auto-drafted invitations for candidates from the **sample resume pipeline** "
                "(Run Pipeline button above) who crossed the 70% shortlist threshold."
            )
            send_btn = gr.Button("📤  Send All Interview Emails to Shortlisted Candidates", variant="primary")
            send_status = gr.HTML()
            emails_out = gr.HTML()

        with gr.Tab("📥  Replies"):
            gr.Markdown("Checks the Gmail inbox above for replies **from the shortlisted candidates** you emailed.")
            refresh_btn = gr.Button("🔄  Check for Replies", variant="primary")
            replies_out = gr.HTML()

    run_btn.click(
        fn=execute_pipeline,
        inputs=[role_dd],
        outputs=[stats_out, table_out, score_plot, skills_plot, emails_out, emails_state],
    )

    demo.load(
        fn=execute_pipeline,
        inputs=[role_dd],
        outputs=[stats_out, table_out, score_plot, skills_plot, emails_out, emails_state],
    )

    fetch_apps_btn.click(
        fn=analyze_inbox_applications,
        inputs=[gmail_email_in, gmail_app_password_in],
        outputs=[applications_status, table_out, score_plot, skills_plot, candidate_checkbox, inbox_state],
    )

    send_selected_btn.click(
        fn=send_invitations_to_selected,
        inputs=[sender_email_in, sender_name_in, brevo_key_in, candidate_checkbox, inbox_state],
        outputs=[send_selected_status],
    )

    send_btn.click(
        fn=send_all_emails,
        inputs=[sender_email_in, sender_name_in, brevo_key_in, emails_state],
        outputs=[send_status],
    )

    refresh_btn.click(
        fn=check_replies,
        inputs=[gmail_email_in, gmail_app_password_in, emails_state],
        outputs=[replies_out],
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)