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
import re
import html as _html
import imaplib
import email as email_lib
import email.utils
import email.header
import smtplib
import requests
import zipfile
from io import BytesIO
from collections import Counter
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pypdf
from src import skills as skills_mod

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import gradio as gr

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
.gradio-container {max-width: 100% !important; width: 100% !important; margin: 0 !important;
                    padding-left: 28px !important; padding-right: 28px !important; box-sizing: border-box;}

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
.skills-row {display:flex; flex-wrap:wrap; gap:5px; max-width:430px;}
.skill-chip {font-size:0.68rem; padding:2px 8px; border-radius:999px;
             background:rgba(139,92,246,0.12); color:#c4b5fd; border:1px solid rgba(139,92,246,0.25);}
.skill-chip.missing {background:rgba(244,63,94,0.08); color:#fca5a5; border-color:rgba(251,113,133,0.2);}


/* ---------- Creator credit ---------- */
.credit-line {display:flex; align-items:center; justify-content:center; gap:10px; flex-wrap:wrap;
              margin: 6px 0 2px 0; color:#8b8ba7; font-size:0.82rem;}
.credit-line b {color:#e9d5ff; font-weight:700;}
.social-pill {display:inline-flex; align-items:center; gap:7px; padding:5px 12px; border-radius:999px;
              font-size:0.78rem; font-weight:600; text-decoration:none !important; color:#e4e4f0 !important;
              background:#181826; border:1px solid #2f2f47; transition: all .18s ease;}
.social-pill svg {width:15px; height:15px; fill:currentColor;}
.social-pill:hover {transform: translateY(-1px); color:#ffffff !important;}
.social-pill.li:hover {background:#0a66c2; border-color:#0a66c2; box-shadow:0 4px 14px rgba(10,102,194,.45);}
.social-pill.gh:hover {background:#2b2b3f; border-color:#a855f7; box-shadow:0 4px 14px rgba(168,85,247,.35);}
#credit-footer {text-align:center; padding:22px 0 10px 0; margin-top:14px; border-top:1px solid #232336;}

footer {display:none !important;}

/* ---------- Kill Chrome's white autofill background on inputs ---------- */
input:-webkit-autofill,
input:-webkit-autofill:hover,
input:-webkit-autofill:focus,
input:-webkit-autofill:active {
  -webkit-box-shadow: 0 0 0 1000px #1a1a29 inset !important;
  box-shadow: 0 0 0 1000px #1a1a29 inset !important;
  -webkit-text-fill-color: #e4e4f0 !important;
  caret-color: #e4e4f0 !important;
  transition: background-color 9999s ease-in-out 0s;
}

/* ---------- Credit footer ---------- */
#credit-footer {text-align:center; padding: 28px 0 14px 0; margin-top: 10px;
                border-top: 1px solid #1d1d2c;}
#credit-footer .made-by {color:#8b8ba7; font-size:0.82rem; margin-bottom:8px;}
#credit-footer .made-by b {color:#c4b5fd; font-weight:600;}
#credit-footer .credit-links {display:flex; gap:10px; justify-content:center;}
#credit-footer .credit-links a {font-size:0.78rem; padding:6px 16px; border-radius:999px;
       color:#c4b5fd; background: rgba(139,92,246,0.1); border:1px solid rgba(139,92,246,0.28);
       text-decoration:none; transition: background 0.15s;}
#credit-footer .credit-links a:hover {background: rgba(139,92,246,0.22); color:#e9d5ff;}

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



LINKEDIN_URL = "https://linkedin.com/in/hamzaasif-ai"
GITHUB_URL = "https://github.com/Hamza-Asif-ai"

_LI_SVG = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>')
_GH_SVG = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>')


def creator_credit_html():
    """'Made by' line with clickable LinkedIn / GitHub pills (open in a new tab)."""
    return (
        '<div class="credit-line">'
        '<span>Designed &amp; built by <b>Hamza Asif</b></span>'
        f'<a class="social-pill li" href="{LINKEDIN_URL}" target="_blank" rel="noopener noreferrer" '
        f'title="linkedin.com/in/hamzaasif-ai">{_LI_SVG}LinkedIn</a>'
        f'<a class="social-pill gh" href="{GITHUB_URL}" target="_blank" rel="noopener noreferrer" '
        f'title="github.com/Hamza-Asif-ai">{_GH_SVG}GitHub</a>'
        '</div>'
    )


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
        missing = c.get("missing_required", []) or []
        chips = "".join(f'<span class="skill-chip">✓ {_html.escape(s)}</span>' for s in matched[:6])
        if len(matched) > 6:
            chips += f'<span class="skill-chip">+{len(matched) - 6} more</span>'
        chips += "".join(f'<span class="skill-chip missing">✗ {_html.escape(s)}</span>' for s in missing[:4])
        if len(missing) > 4:
            chips += f'<span class="skill-chip missing">+{len(missing) - 4} missing</span>'

        yrs = c.get("experience_years", 0) or 0
        need = c.get("_min_years", 0) or 0
        exp_txt = f"{yrs:g} / {need} yrs" if need else f"{yrs:g} yrs"
        exp_style = "color:#fca5a5;" if need and yrs < need else "color:#dcdce8;"

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
          <td style="white-space:nowrap; {exp_style}">{exp_txt}</td>
          <td><div class="skills-row">{chips}</div></td>
          <td><span class="chip {cls}">{label}</span></td>
        </tr>""")
    if not rows_html:
        return '<div class="empty-state">No candidates yet — fill in the job requirements and scan the inbox.</div>'
    return f"""
    <div class="cand-table-wrap">
    <table class="cand-table">
      <thead><tr><th>#</th><th>Candidate</th><th>Match Score</th><th>Experience (has / needs)</th><th>Skills (✓ found · ✗ missing)</th><th>Recommendation</th></tr></thead>
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
    Returns (status_html, list_of_successfully_sent_email_dicts).
    """
    if not sender_email or not brevo_api_key:
        return '<div class="status-line err">⚠ Please enter both the sender email and the Brevo API key.</div>', []
    if not emails_state:
        return '<div class="status-line err">⚠ No emails to send.</div>', []

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": brevo_api_key,
        "content-type": "application/json",
    }
    lines = []
    sent_ok = []
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
                sent_ok.append(e)
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
    return "".join(lines), sent_ok


def _decode_mime_header(value: str) -> str:
    """Decode MIME encoded-word headers (e.g. '=?UTF-8?Q?...?=') into plain text."""
    if not value:
        return value
    try:
        parts = email.header.decode_header(value)
        decoded = []
        for text, charset in parts:
            if isinstance(text, bytes):
                decoded.append(text.decode(charset or "utf-8", errors="ignore"))
            else:
                decoded.append(text)
        return "".join(decoded)
    except Exception:
        return value


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


def check_replies(sender_email, app_password, emails_state=None):
    """
    Scans the inbox for genuine replies (Re: subject, or In-Reply-To/References
    headers) — independent of whether anything was sent earlier in this browser
    session, since gr.State resets on page reload and shouldn't gate this.
    If emails_state (candidates invited this session) is available, it's used
    only to label which replies came from an invited candidate — never to hide
    results when it's empty.
    """
    if not sender_email or not app_password:
        return '<div class="status-line err">⚠ Please enter both the Gmail address and the App Password.</div>'

    candidate_addrs = {e["to"].lower() for e in (emails_state or [])}
    replies = []
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(sender_email, app_password)
        imap.select("INBOX")
        status, data = imap.search(None, "ALL")
        ids = data[0].split()[-150:]  # last 150 messages, newest last
        for msg_id in reversed(ids):
            status, msg_data = imap.fetch(msg_id, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            subject = _decode_mime_header(msg.get("Subject", "(no subject)"))
            # Only real replies — not a candidate's original application email,
            # which can sit in the same inbox from the same address.
            is_reply = (subject.strip().lower().startswith("re:")
                        or msg.get("In-Reply-To") is not None
                        or msg.get("References") is not None)
            if not is_reply:
                continue
            from_addr = email.utils.parseaddr(msg.get("From", ""))[1].lower()
            body = _extract_plain_body(msg).strip()
            replies.append({
                "from": from_addr,
                "subject": subject,
                "date": msg.get("Date", ""),
                "body": body[:600],
                "invited": from_addr in candidate_addrs,
            })
        imap.logout()
    except imaplib.IMAP4.error as ex:
        return f'<div class="status-line err">❌ IMAP error: {_html.escape(str(ex))} (check the App Password)</div>'
    except Exception as ex:
        return f'<div class="status-line err">❌ Error: {_html.escape(str(ex))}</div>'

    if not replies:
        return '<div class="empty-state">No replies found in the inbox yet.</div>'

    cards = []
    for r in replies:
        badge = '<span class="attach-chip">✓ invited this session</span>' if r["invited"] else ""
        cards.append(f"""
        <div class="reply-card">
          <span class="reply-date">{_html.escape(r['date'])}</span>
          <div class="email-to">{_html.escape(r['from'])} {badge}</div>
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
        if not low.endswith((".pdf", ".docx", ".txt", ".rtf")):
            # legacy binary .doc needs extra libraries; fall back to the email body instead.
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        if low.endswith(".docx"):
            try:
                with zipfile.ZipFile(BytesIO(payload)) as zf:
                    xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
                xml = re.sub(r"</w:p>", "\n", xml)
                xml = re.sub(r"<w:tab/>|<w:br/>", " ", xml)
                text = _html.unescape(re.sub(r"<[^>]+>", "", xml))
                return filename, text
            except Exception:
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


def render_application_email_cards(candidates):
    if not candidates:
        return ""
    cards = []
    for c in candidates:
        att = (f'<span class="attach-chip">📎 {_html.escape(c["_attachment"])}</span>'
               if c.get("_attachment") else "")
        cls, label = REC_CHIP.get(c["recommendation"], ("review", c["recommendation"]))
        body_text = c.get("_body", "") or "(no body content found)"
        cards.append(f"""
        <div class="reply-card">
          <span class="reply-date">{_html.escape(c.get('_date', ''))}</span>
          <div class="email-to">{_html.escape(c['name'])}</div>
          <div class="email-addr">{_html.escape(c['email'])}</div>
          <div class="email-subject">✉ {_html.escape(c.get('_subject', '') or '(no subject)')} {att}</div>
          <div class="email-body" style="max-height:260px; overflow-y:auto;">{_html.escape(body_text)}</div>
          <div style="margin-top:10px;">
            <span class="chip {cls}">{label}</span>
            <span class="match-pct" style="margin-left:8px;">{c['match_percent']}% match</span>
          </div>
        </div>""")
    return f'<div class="reply-grid">{"".join(cards)}</div>'


def _stats_html_for(candidates):
    total = len(candidates)
    shortlisted = sum(1 for c in candidates if c["recommendation"] == "Strong match - shortlist")
    rate = round((shortlisted / total * 100), 1) if total else 0
    avg = round(sum(c["match_percent"] for c in candidates) / total, 1) if total else 0
    return (
        '<div style="display:grid; grid-template-columns:repeat(4,1fr); gap:14px;">'
        + stat_card(total, "Applicants")
        + stat_card(shortlisted, "Shortlisted")
        + stat_card(f'{rate}%', "Shortlist Rate")
        + stat_card(f'{avg}%', "Avg. Match")
        + "</div>"
    )


# --------------------------------------------------------------------------- #
# HR-defined job requirements: position, skills, experience
# --------------------------------------------------------------------------- #
def _parse_skill_list(text, limit=25):
    """'Python, SQL; docker' -> ['python', 'sql', 'docker'] (lower-cased, de-duplicated)."""
    seen, out = set(), []
    for token in re.split(r"[,;\n]+", text or ""):
        t = re.sub(r"\s+", " ", token.strip().lower())
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:limit]


_AMBIGUOUS_SKILLS = {"go", "r", "c"}


def _skill_present(skill, text_norm):
    """True if `skill` appears in the (lower-cased, whitespace-normalised) resume text.
    Skills the project already knows (SKILL_VOCAB) also match their aliases
    (e.g. kubernetes == k8s); any other skill is matched as a whole word/phrase."""
    if skill in _AMBIGUOUS_SKILLS:
        # "go", "r", "c" are also ordinary words / letters -> only count them in a list or
        # "<x> developer / programming / language" context (plus "golang" for go).
        if skill == "go" and re.search(r"(?<![a-z0-9])golang", text_norm):
            return True
        return re.search(r"(?<![a-z0-9])" + re.escape(skill) +
                         r"(?=\s*[,;/|•·)]|\s+(?:developer|programming|language|lang)\b)", text_norm) is not None

    aliases = skills_mod.SKILL_VOCAB.get(skill)
    if aliases:
        # alias must start at a word boundary (so "go " does not fire inside "django "),
        # but may continue (so "microservice" still matches "microservices")
        for a in aliases:
            if re.search(r"(?<![a-z0-9])" + re.escape(a), text_norm):
                return True
    words = [re.escape(w) for w in re.split(r"[\s\-_]+", skill) if w]
    if not words:
        return False
    pattern = r"(?<![a-z0-9])" + r"[\s\-_]+".join(words) + r"(?![a-z0-9])"
    return re.search(pattern, text_norm) is not None


_EDU_HEADINGS = {"education", "academic background", "academics", "academic qualifications",
                 "educational background", "qualifications"}
_OTHER_HEADINGS = {"experience", "work experience", "professional experience", "employment",
                   "employment history", "work history", "projects", "skills", "technical skills",
                   "certifications", "summary", "profile", "objective", "achievements",
                   "internships", "internship", "references", "languages", "interests", "awards",
                   "publications", "courses", "training", "volunteering", "professional summary"}
_EDU_LINE = re.compile(r"\b(university|college|bachelors?|bsc|b\.sc|msc|m\.sc|mba|ph\.?d|matric|"
                       r"intermediate|fsc|cgpa|gpa)\b", re.I)
_RANGE = re.compile(
    r"((?:19|20)\d{2})\s*(?:-|–|—|to|until)\s*(?:[A-Za-z]{3,9}\.?,?\s*)?"
    r"((?:19|20)\d{2}|present|current|now|date)", re.I)
_EXPLICIT_YEARS = re.compile(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)\b", re.I)


def _estimate_experience_years(text):
    """Total professional experience in years.
    Takes the larger of (a) an explicit claim such as '5 years of experience' and
    (b) the merged length of dated job ranges such as 'Jan 2020 - Present',
    ignoring date ranges that belong to the education section."""
    from datetime import datetime
    explicit = [float(x) for x in _EXPLICIT_YEARS.findall(text) if float(x) <= 45]

    this_year = datetime.now().year
    in_edu, spans = False, []
    for line in text.splitlines():
        head = re.sub(r"[^a-z ]", "", line.lower()).strip()
        if 0 < len(head.split()) <= 4:
            if head in _EDU_HEADINGS:
                in_edu = True
            elif head in _OTHER_HEADINGS:
                in_edu = False
        if in_edu or _EDU_LINE.search(line):
            continue
        for a, b in _RANGE.findall(line):
            start = int(a)
            end = this_year if not b.isdigit() else int(b)
            if start <= end <= this_year + 1 and end - start <= 40:
                spans.append((start, end))

    spans.sort()
    total, cur = 0.0, None
    for start, end in spans:
        if cur is None:
            cur = [start, end]
        elif start <= cur[1]:
            cur[1] = max(cur[1], end)
        else:
            total += max(cur[1] - cur[0], 0.5)
            cur = [start, end]
    if cur is not None:
        total += max(cur[1] - cur[0], 0.5)

    return round(max(max(explicit, default=0.0), total), 1)


def build_custom_requirements(position, skills_text, nice_text, min_years):
    try:
        min_years = max(int(min_years or 0), 0)
    except (ValueError, TypeError):
        min_years = 0
    required = _parse_skill_list(skills_text)
    nice = [s for s in _parse_skill_list(nice_text) if s not in required]
    return {
        "title": (position or "").strip() or "the role",
        "required_skills": required,
        "nice_to_have": nice,
        "min_years": min_years,
        "seniority": "senior" if min_years >= 5 else ("mid" if min_years >= 2 else "junior"),
        "qualifications": [],
    }


def _draft_invitation(candidate, position, company, recruiter, slot):
    """Role-neutral interview invitation (no hard-coded company / team / time zone)."""
    first = (candidate.get("name") or "there").split()[0]
    strengths = candidate.get("matched_required") or candidate.get("skills") or []
    strengths_str = ", ".join(strengths[:3]) if strengths else "your background"
    company = (company or "").strip() or "our company"
    recruiter = (recruiter or "").strip() or "HR Team"
    slot = re.sub(r"\s+IST$", "", slot or "").strip()
    body = (
        f"Dear {first},\n\n"
        f"Thank you for applying for the {position} position at {company}. "
        f"After reviewing your application, we were impressed by your experience "
        f"with {strengths_str} and would like to invite you to an interview.\n\n"
        f"Proposed slot: {slot}\n"
        f"Format: 45-minute video call\n\n"
        f"Please reply to confirm whether this time works, or suggest an "
        f"alternative that suits you better.\n\n"
        f"We look forward to speaking with you.\n\n"
        f"Best regards,\n{recruiter}\nTalent Acquisition, {company}"
    )
    return {
        "to": candidate.get("email", ""),
        "candidate": candidate.get("name", ""),
        "subject": f"Interview Invitation - {position} at {company}",
        "body": body,
        "slot": slot,
    }


def _recommendation_for(match):
    # Same thresholds as skills.candidate_scorer_skill
    if match >= 75:
        return "Strong match - shortlist"
    if match >= 55:
        return "Possible match - review"
    return "Weak match - likely reject"


def analyze_inbox_applications(gmail_address, app_password, position, skills_text, nice_text, min_years, limit=60):
    """
    Scans the given Gmail inbox for candidate application emails, reads each
    resume (PDF/TXT attachment text, else the email body) and scores it against
    the position / skills / experience the HR entered in the UI.
    """
    empty_state = {"title": "", "candidates": []}
    empty_stats = _stats_html_for([])

    def _fail(html_msg):
        return (empty_stats, html_msg, render_candidate_table([]), make_score_chart([]),
                make_skills_chart([]), gr.update(choices=[], value=[]), empty_state, "")

    if not (position or "").strip():
        return _fail('<div class="status-line err">⚠ Enter the position you are hiring for.</div>')
    if not _parse_skill_list(skills_text):
        return _fail('<div class="status-line err">⚠ Enter at least one required skill (comma separated).</div>')
    if not gmail_address or not app_password:
        return _fail('<div class="status-line err">⚠ Please enter both the Gmail address and the App Password.</div>')

    requirements = build_custom_requirements(position, skills_text, nice_text, min_years)
    wanted = requirements["required_skills"] + requirements["nice_to_have"]

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
            msg = email_lib.message_from_bytes(msg_data[0][1])
            subject = _decode_mime_header(msg.get("Subject", "") or "")
            body = _extract_plain_body(msg)
            attachment_name, attachment_text = _extract_attachment_text(msg)
            blob = f"{subject} {body}".lower()
            has_attachment = _find_attachment_name(msg) is not None
            if not (has_attachment or any(k in blob for k in _APPLICATION_KEYWORDS)):
                continue

            from_name, from_addr = email.utils.parseaddr(msg.get("From", ""))
            from_name = _decode_mime_header(from_name)
            resume_text = attachment_text.strip() if attachment_text.strip() else body
            text_norm = re.sub(r"\s+", " ", resume_text.lower())

            profile = skills_mod.resume_parser_skill(resume_text, source_id=from_addr or subject)
            profile["name"] = from_name or (from_addr.split("@")[0] if from_addr else "Unknown Candidate").title()
            if from_addr:
                profile["email"] = from_addr
            # Skills = exactly the ones HR asked for that this resume contains.
            profile["skills"] = [sk for sk in wanted if _skill_present(sk, text_norm)]
            profile["experience_years"] = _estimate_experience_years(resume_text)

            result = skills_mod.candidate_scorer_skill(profile, requirements)
            if not requirements["nice_to_have"]:
                # No nice-to-have list -> drop that 15% component and re-normalise to 100.
                result["match_percent"] = min(100.0, round(result["match_percent"] / 0.85, 1))
                result["recommendation"] = _recommendation_for(result["match_percent"])
            result["email"] = profile["email"]
            result["skills"] = profile["skills"]
            result["name"] = profile["name"]
            result["_min_years"] = requirements["min_years"]
            result["_subject"] = subject
            result["_attachment"] = attachment_name
            result["_body"] = body.strip()
            result["_date"] = msg.get("Date", "")
            candidates.append(result)
        imap.logout()
    except imaplib.IMAP4.error as ex:
        return _fail(f'<div class="status-line err">❌ IMAP error: {_html.escape(str(ex))} (check the App Password)</div>')
    except Exception as ex:
        return _fail(f'<div class="status-line err">❌ Error: {_html.escape(str(ex))}</div>')

    if not candidates:
        return _fail('<div class="empty-state">No candidate application emails detected in this inbox.</div>')

    total_found = len(candidates)
    # Drop applications with zero overlap with the required skills — these are for a
    # different field entirely (e.g. a marketing resume against a backend role) and
    # shouldn't clutter the ranked list for this position.
    candidates = [c for c in candidates if c.get("matched_required")]
    unrelated_count = total_found - len(candidates)

    if not candidates:
        msg = (f'<div class="status-line err">⚠ Found {total_found} application email(s), but none matched '
               f'any of the required skills for "{_html.escape(requirements["title"])}" — they look like '
               f'applications for a different role.</div>')
        return _stats_html_for([]), msg, render_candidate_table([]), make_score_chart([]), make_skills_chart([]), gr.update(choices=[], value=[]), empty_state, ""

    candidates.sort(key=lambda c: c["match_percent"], reverse=True)
    for i, c in enumerate(candidates, 1):
        c["rank"] = i

    coverage = sorted(
        ((sk, sum(1 for c in candidates if sk in c["skills"])) for sk in wanted[:10]),
        key=lambda kv: kv[1], reverse=True,
    )

    choice_tuples = [
        (f"#{c['rank']} {c['name']} — {c['match_percent']}% — {c['email']}", c["email"])
        for c in candidates
    ]
    req_txt = ", ".join(requirements["required_skills"])
    excluded_note = (f' · {unrelated_count} unrelated application(s) excluded (no matching skills)'
                      if unrelated_count else '')
    status = (f'<div class="status-line ok">✅ Found {len(candidates)} relevant application(s), scored for '
              f'“{_html.escape(requirements["title"])}” · required: {_html.escape(req_txt)} · '
              f'min. experience: {requirements["min_years"]} yr(s){excluded_note}</div>'
              f'<div class="status-line info">Shortlist ≥ 75% · Review 55–74% · Reject &lt; 55%</div>')
    state = {"title": requirements["title"], "candidates": candidates}
    return (_stats_html_for(candidates), status, render_candidate_table(candidates),
            make_score_chart(candidates), make_skills_chart(coverage),
            gr.update(choices=choice_tuples, value=[]), state, render_application_email_cards(candidates))


def send_invitations_to_selected(sender_email, sender_name, brevo_api_key, company,
                                 selected_emails, inbox_state, sent_invitations_state):
    sent_invitations_state = sent_invitations_state or []
    if not selected_emails:
        return ('<div class="status-line err">⚠ Select at least one candidate first (checkboxes above).</div>',
                sent_invitations_state, render_emails(sent_invitations_state))

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
        drafts.append(_draft_invitation(c, title, company, sender_name, slot))

    if not drafts:
        return ('<div class="status-line err">⚠ Could not match the selected candidates — try scanning the inbox again.</div>',
                sent_invitations_state, render_emails(sent_invitations_state))

    status_html, sent_ok = send_all_emails(sender_email, sender_name, brevo_api_key, drafts)
    updated_state = sent_invitations_state + sent_ok
    return status_html, updated_state, render_emails(updated_state)


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
with gr.Blocks(theme=THEME, css=CSS, title="Smart HR Recruitment") as demo:
    sent_invitations_state = gr.State([])
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
    """ + creator_credit_html())

    gr.Markdown("### 🎯 What are you hiring for?  \nCandidates are detected, ranked, shortlisted and recommended against these requirements.")
    with gr.Row():
        position_in = gr.Textbox(label="Position / job title", placeholder="e.g. Data Analyst", scale=3)
        company_in = gr.Textbox(label="Company name (used in invitation emails)", placeholder="e.g. Acme Pvt Ltd", scale=3)
        min_exp_in = gr.Number(label="Minimum experience (years)", value=0, minimum=0, precision=0, scale=1)
    with gr.Row():
        skills_in = gr.Textbox(label="Required skills (comma separated)", placeholder="e.g. python, sql, power bi, excel", lines=2, scale=3)
        nice_in = gr.Textbox(label="Nice-to-have skills (optional)", placeholder="e.g. tableau, aws", lines=2, scale=2)

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
            "2. Get your key under **Transactional → Settings → API Keys → Generate a new API key**\n"
            "3. ⚠ Brevo also requires **phone verification** on your account before it will send its "
            "first email — check the banner at the top of your Brevo dashboard if sending fails."
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
                "each one against the role details entered above, and feeds the full results into the "
                "other tabs."
            )
            fetch_apps_btn = gr.Button("🔍  Scan Inbox for Candidate Applications", variant="primary")
            applications_status = gr.HTML()
            applications_cards_out = gr.HTML()

        with gr.Tab("🏆  Ranked Candidates"):
            table_out = gr.HTML()
            gr.Markdown("**Select candidates to invite for an interview:**")
            candidate_checkbox = gr.CheckboxGroup(choices=[], label="Detected candidates (ranked highest match first)")
            send_selected_btn = gr.Button("📤  Send Interview Invitation to Selected", variant="primary")
            send_selected_status = gr.HTML()

        with gr.Tab("📊  Dashboard"):
            with gr.Row():
                score_plot = gr.Plot(label="Candidate Match Scores")
                skills_plot = gr.Plot(label="Required-skill coverage (candidates who have each skill)")

        with gr.Tab("✉️  Interview Emails"):
            gr.Markdown(
                "Shows the full content of every interview invitation **actually sent** so far "
                "(from the Ranked Candidates tab's Send button)."
            )
            emails_out = gr.HTML('<div class="empty-state">No invitations sent yet this session.</div>')

        with gr.Tab("📥  Replies"):
            gr.Markdown("Checks the Gmail inbox above for replies **from the candidates you've invited**.")
            refresh_btn = gr.Button("🔄  Check for Replies", variant="primary")
            replies_out = gr.HTML()

    gr.HTML('<div id="credit-footer">' + creator_credit_html() + '</div>')

    fetch_apps_btn.click(
        fn=analyze_inbox_applications,
        inputs=[gmail_email_in, gmail_app_password_in, position_in, skills_in, nice_in, min_exp_in],
        outputs=[stats_out, applications_status, table_out, score_plot, skills_plot, candidate_checkbox, inbox_state, applications_cards_out],
    )

    send_selected_btn.click(
        fn=send_invitations_to_selected,
        inputs=[sender_email_in, sender_name_in, brevo_key_in, company_in, candidate_checkbox, inbox_state, sent_invitations_state],
        outputs=[send_selected_status, sent_invitations_state, emails_out],
    )

    refresh_btn.click(
        fn=check_replies,
        inputs=[gmail_email_in, gmail_app_password_in, sent_invitations_state],
        outputs=[replies_out],
    )

    gr.HTML("""
      <div id="credit-footer">
        <div class="made-by">Made by <b>Hamza Asif</b></div>
        <div class="credit-links">
          <a href="https://linkedin.com/in/hamzaasif-ai" target="_blank" rel="noopener">🔗 LinkedIn</a>
          <a href="https://github.com/Hamza-Asif-ai" target="_blank" rel="noopener">💻 GitHub</a>
        </div>
      </div>
    """)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)