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
    - Reads Gmail through the read-only Gmail API using Google OAuth.
    - Sends interview invitations through Brevo; the Brevo key is entered in
      the UI at runtime and is never written to disk.
"""

from __future__ import annotations

import base64
import os
import re
import html as _html
import email as email_lib
import email.utils
import email.header
import requests
import zipfile
import threading
from io import BytesIO
from collections import Counter

import pypdf
from src import auth as auth_mod
from src import calendar_api
from src import config
from src import exporters
from src import extractor as extractor_mod
from src import gmail_api
from src import google_oauth
from src import guardrails as guardrails_mod
from src import phase3_runner
from src import runtime
from src import scorecard
from src import skills as skills_mod
from src import workspace as workspace_mod
from src.approval import (
    ApprovalQueue,
    ApprovalRequiredError,
    send_approved_draft,
)
from src.memory import CandidateMemory
from src.security import (
    AccessDeniedError,
    Role,
    SecurityContext,
    has_header_injection,
    is_valid_email,
    safe_error,
    scrub_secrets,
)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import gradio as gr

import json
os.environ.setdefault("HR_DATA_ROOT", "./data")


# --------------------------------------------------------------------------- #
# Startup validation and per-session identity
# --------------------------------------------------------------------------- #
_MEMORY_STORE = None
_MEMORY_LOCK = threading.Lock()


def _candidate_memory():
    """Return the process-wide memory store (keyed by HMAC(email) only)."""
    global _MEMORY_STORE
    with _MEMORY_LOCK:
        if _MEMORY_STORE is None:
            try:
                _MEMORY_STORE = CandidateMemory()
            except Exception:
                _MEMORY_STORE = None
        return _MEMORY_STORE


def _remember_candidate(email, decision, reason=""):
    store = _candidate_memory()
    if store is None or not email:
        return
    try:
        store.remember_candidate(email, decision, reason)
    except Exception:
        pass


def validate_startup():
    """Fail closed when no HR users are configured unless explicitly local-only."""
    auth_manager = auth_mod.get_auth_manager()
    runtime.cleanup_temp_files()
    # Validate GEMINI_MODEL at startup, not on the first user request.
    try:
        phase3_runner.model_status()
    except Exception:
        pass
    return auth_manager


def _identity_from_request(request):
    """Resolve the signed-in user for a Gradio event.

    ``request is None`` only happens for direct Python calls/tests, never for a
    network request handled by Gradio, so it is treated as the local admin.
    """
    if request is None:
        return auth_mod.UserRecord(username="system", role=Role.ADMIN,
                                   password_hash=""), None
    manager = auth_mod.get_auth_manager()
    if not manager.enabled:
        return auth_mod.UserRecord(username="local", role=Role.ADMIN,
                                   password_hash=""), manager
    username = str(getattr(request, "username", "") or "").strip().lower()
    record = manager.users.get(username)
    if record is None:
        raise AccessDeniedError("Not signed in.")
    return record, manager


def _authorized(request, capability):
    """True when the current session may perform ``capability``."""
    record, manager = _identity_from_request(request)
    ctx = SecurityContext(user=record.username, role=record.role)
    try:
        ctx.authorize(capability)
        return True
    except AccessDeniedError:
        if manager is not None:
            manager.audit_event("access_denied", record.username, record.role, capability)
        return False


def _denied_html(capability=""):
    detail = f" ({_html.escape(capability)})" if capability else ""
    return f'<div class="status-line err">⛔ Your role is not allowed to do that{detail}.</div>'


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

/* ---------- Candidate score cards ---------- */
.score-card-grid {display:grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
                  gap:16px; align-items:start;}
.score-card {border:1px solid #26263a; border-radius:16px; padding:18px;
             background: linear-gradient(165deg, rgba(99,102,241,0.10), rgba(18,18,28,0.96));}
.score-card-head {display:flex; align-items:flex-start; justify-content:space-between; gap:12px;}
.score-card-name {font-weight:700; color:#f1f1f8; font-size:1rem;}
.score-card-score {text-align:right; flex-shrink:0;}
.score-card-score .match-pct {font-size:1.2rem; width:auto;}
.score-card-meta {color:#8b8ba7; font-size:0.75rem; margin:8px 0 12px 0;}
.score-card-section-title {color:#9694b5; font-size:0.7rem; letter-spacing:.08em;
                           text-transform:uppercase; font-weight:700; margin:14px 0 7px 0;}
.score-quote {border-left:2px solid #6366f1; background:rgba(99,102,241,0.07);
              border-radius:0 8px 8px 0; padding:8px 11px; margin-bottom:7px;}
.score-quote-label {color:#a5b4fc; font-size:0.7rem; font-weight:700; text-transform:uppercase;
                    letter-spacing:.04em;}
.score-quote-text {color:#dcdce8; font-size:0.82rem; line-height:1.45; margin-top:2px;}
.why-not {margin-top:14px; border:1px solid rgba(251,113,133,0.25); border-radius:10px;
          background:rgba(244,63,94,0.06); padding:10px 12px;}
.why-not-title {color:#fca5a5; font-size:0.74rem; font-weight:700; text-transform:uppercase;
                letter-spacing:.05em; margin-bottom:5px;}
.why-not ul {margin:0; padding-left:18px; color:#fecdd3; font-size:0.8rem; line-height:1.5;}


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



def creator_credit_html():
    """Generic project credit (no personal profiles or contact details)."""
    return (
        '<div class="credit-line">'
        '<span>Smart HR Recruitment · built with Google ADK</span>'
        '</div>'
    )


def stat_card(number, label):
    return (f'<div class="stat-card"><div class="num">{_html.escape(str(number))}</div>'
            f'<div class="lbl">{_html.escape(str(label))}</div></div>')


REC_CHIP = {
    "Strong match - shortlist": ("shortlist", "Shortlist"),
    "Possible match - review": ("review", "Review"),
    "Possible match - review (keyword stuffing suspected)": ("review", "Review"),
    "Weak match - likely reject": ("reject", "Reject"),
    "Unreadable resume - manual review": ("review", "Manual review"),
}


def render_candidate_table(ranked):
    rows_html = []
    for c in ranked:
        cls, label = REC_CHIP.get(c["recommendation"], ("review", c["recommendation"]))
        label = _html.escape(str(label))
        rank_cls = "rank-badge top" if c["rank"] <= 3 else "rank-badge"
        name = _html.escape(c["name"])
        email_addr = _html.escape(c["email"])
        pct = c["match_percent"]
        conf = c.get("confidence", c.get('_confidence', 'N/A'))
        mode = c.get("extraction_mode", "deterministic")
        review = bool(c.get("needs_human_review"))
        hint = c.get("memory_hint")
        review_html = ('<span class="chip review" style="margin-left:6px;">Human review</span>'
                       if review else "")
        hint_html = (f'<div class="cand-email">🧠 {_html.escape(str(hint))}</div>'
                     if hint else "")

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
          <td><div class="cand-name">{name}</div><div class="cand-email">{email_addr}</div>{hint_html}</td>
          <td>
            <div class="match-cell" style="flex-direction:column; align-items:flex-start; gap:4px;">
              <div style="display:flex; align-items:center; gap:8px; width:100%;">
                <div class="match-bar-bg"><div class="match-bar-fill" style="width:{pct}%;"></div></div>
                <div class="match-pct">{pct}%</div>
              </div>
              <div style="font-size:0.75rem; color:#a5b4fc;">{pct}% Match · {conf}% Conf · {_html.escape(str(mode))}</div>
            </div>
          </td>
          <td style="white-space:nowrap; {exp_style}">{exp_txt}</td>
          <td><div class="skills-row">{chips}</div></td>
          <td><span class="chip {cls}">{label}</span>{review_html}</td>
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
        name = (e.get("candidate") or "").strip() or _extract_first_name(e.get("body", ""), fallback)
        initials = _html.escape("".join([p[0].upper() for p in name.split()[:2]]) or "?")
        subject = _html.escape(e["subject"])
        body = _bold_inline(e.get("body_md") or e["body"])
        to_addr = _html.escape(e["to"])
        ics_chip = (f'<span class="attach-chip">📅 {_html.escape(str(e.get("ics_filename")))}</span>'
                    if e.get("ics_filename") else "")
        cards.append(f"""
        <div class="email-card">
          <div class="email-top">
            <div class="email-avatar">{initials}</div>
            <div>
              <div class="email-to">{_html.escape(name)}</div>
              <div class="email-addr">{to_addr}</div>
            </div>
          </div>
          <div class="email-subject">✉ {subject} {ics_chip}</div>
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
    threshold = config.SHORTLIST_THRESHOLD
    colors = ["#a855f7" if s >= threshold else "#3f3f52" for s in scores]

    fig, ax = plt.subplots(figsize=(7.6, max(3.6, n * 0.34)), facecolor="#12121c")
    ax.set_facecolor("#12121c")
    bars = ax.barh(names[::-1], scores[::-1], color=colors[::-1], height=0.58)
    ax.axvline(threshold, color="#f472b6", linestyle="--", linewidth=1, alpha=0.7)
    ax.text(threshold + 1, n - 0.5, f"{threshold:.0f}% shortlist line", color="#f472b6", fontsize=8)
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
def _error_html(ex, secrets=()):
    """Escape and scrub exception text before it reaches the UI."""
    return _html.escape(safe_error(ex, secrets))


def send_all_emails(sender_email, sender_name, brevo_api_key, emails_state):
    """
    Sends interview emails via the Brevo transactional email HTTP API.
    Uses HTTPS (port 443) so it works from any host, including free-tier PaaS
    platforms (Render, etc.) that block outbound SMTP ports 25/465/587.
    Unlike Resend's sandbox domain, Brevo lets you verify a single sender EMAIL
    (no domain purchase needed) and send to any recipient after that.
    Returns (status_html, list_of_successfully_sent_email_dicts).
    """
    sender_email = (sender_email or "").strip()
    sender_name = (sender_name or "HR Team").strip()
    brevo_api_key = (brevo_api_key or "").strip()
    if not sender_email or not brevo_api_key:
        return '<div class="status-line err">⚠ Please enter both the sender email and the Brevo API key.</div>', []
    if not is_valid_email(sender_email):
        return '<div class="status-line err">⚠ The sender email address is not valid.</div>', []
    if has_header_injection(sender_name):
        return '<div class="status-line err">⚠ The sender name contains invalid characters.</div>', []
    if not emails_state:
        return '<div class="status-line err">⚠ No emails to send.</div>', []

    secrets = (brevo_api_key, sender_email)
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": brevo_api_key,
        "content-type": "application/json",
    }

    # Validate, de-duplicate (case-insensitive recipient), and cap one batch.
    lines = []
    unique = []
    seen = set()
    invalid = 0
    duplicates = 0
    for e in emails_state:
        if not isinstance(e, dict):
            invalid += 1
            continue
        recipient = str(e.get("to", "")).strip()
        if not is_valid_email(recipient) or has_header_injection(e.get("subject", "")):
            invalid += 1
            continue
        key = recipient.lower()
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique.append({**e, "to": recipient})

    if invalid:
        lines.append(
            f'<div class="status-line err">⚠ Skipped {invalid} email(s) with an invalid '
            'recipient or header value.</div>'
        )
    if duplicates:
        lines.append(f'<div class="status-line info">ℹ Ignored {duplicates} duplicate recipient(s).</div>')

    batch_limit = max(1, int(config.BREVO_BATCH_LIMIT))
    if len(unique) > batch_limit:
        lines.append(
            f'<div class="status-line info">ℹ Batch capped at {batch_limit} email(s); '
            f'{len(unique) - batch_limit} left for a later batch.</div>'
        )
        unique = unique[:batch_limit]

    sent_ok = []
    try:
        with runtime.QUEUE_GATE.slot(timeout=30):
            for e in unique:
                payload = {
                    "sender": {"name": sender_name or "HR Team", "email": sender_email},
                    "to": [{"email": e["to"]}],
                    "subject": e["subject"],
                    "textContent": e["body"],
                }
                if e.get("html"):
                    payload["htmlContent"] = e["html"]
                ics_content = str(e.get("ics_content", "") or "")
                if ics_content:
                    payload["attachment"] = [{
                        "name": str(e.get("ics_filename") or "interview.ics"),
                        "content": base64.b64encode(ics_content.encode("utf-8")).decode("ascii"),
                    }]
                try:
                    resp = requests.post(
                        url, headers=headers, json=payload,
                        timeout=config.BREVO_TIMEOUT_SECONDS,
                    )
                    if resp.status_code in (200, 201):
                        lines.append(
                            f'<div class="status-line ok">✅ Sent to {_html.escape(e["to"])}</div>'
                        )
                        sent_ok.append(e)
                    else:
                        try:
                            detail = resp.json().get("message", resp.text)
                        except Exception:
                            detail = resp.text
                        detail = scrub_secrets(str(detail), secrets)
                        lines.append(
                            f'<div class="status-line err">❌ {_html.escape(e["to"])} — '
                            f'{resp.status_code}: {_html.escape(detail)}</div>'
                        )
                except Exception as ex:  # noqa: BLE001 - reported without secrets
                    lines.append(
                        f'<div class="status-line err">❌ {_html.escape(e["to"])} — '
                        f'{_error_html(ex, secrets)}</div>'
                    )
    except runtime.QueueFullError:
        return '<div class="status-line err">⚠ The server is busy. Try again shortly.</div>', []

    lines.append(f'<div class="status-line info">📨 {len(unique)} email(s) processed.</div>')
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
    limit = config.IMAP_MAX_TEXT_CHARS
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                try:
                    raw = part.get_payload(decode=True) or b""
                    if len(raw) > limit * 4:
                        continue
                    return raw.decode(
                        part.get_content_charset() or "utf-8", errors="ignore"
                    )[:limit]
                except Exception:
                    continue
        return ""
    try:
        raw = msg.get_payload(decode=True) or b""
        if len(raw) > limit * 4:
            return ""
        return raw.decode(msg.get_content_charset() or "utf-8", errors="ignore")[:limit]
    except Exception:
        return ""


def _gmail_client() -> gmail_api.GmailClient:
    """Return a read-only Gmail client backed by the saved OAuth token."""
    return gmail_api.GmailClient()


def _attachment_payload(part):
    """Decode one attachment only when it fits the configured size limit."""
    limit = config.IMAP_MAX_ATTACHMENT_BYTES
    try:
        raw = part.get_payload(decode=False)
    except Exception:
        raw = None
    if isinstance(raw, str) and len(raw) > (limit * 2) + 4096:
        return None
    try:
        payload = part.get_payload(decode=True)
    except Exception:
        return None
    if not isinstance(payload, (bytes, bytearray)) or len(payload) > limit:
        return None
    return bytes(payload)


def _docx_text(payload: bytes):
    """Extract DOCX text with uncompressed-size, ratio, and XML limits."""
    try:
        with zipfile.ZipFile(BytesIO(payload)) as zf:
            infos = zf.infolist()
            if len(infos) > 10_000:
                return None
            total_uncompressed = sum(max(0, info.file_size) for info in infos)
            total_compressed = sum(max(1, info.compress_size) for info in infos)
            if total_uncompressed > config.DOCX_MAX_UNCOMPRESSED_BYTES:
                return None
            if total_uncompressed / max(1, total_compressed) > config.DOCX_MAX_COMPRESSION_RATIO:
                return None
            document = next((i for i in infos if i.filename == "word/document.xml"), None)
            if document is None or document.file_size > config.DOCX_MAX_XML_BYTES:
                return None
            with zf.open(document) as handle:
                xml_bytes = handle.read(config.DOCX_MAX_XML_BYTES + 1)
            if len(xml_bytes) > config.DOCX_MAX_XML_BYTES:
                return None
        xml = xml_bytes.decode("utf-8", errors="ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:tab/>|<w:br/>", " ", xml)
        text = _html.unescape(re.sub(r"<[^>]+>", "", xml))
        return text[:config.IMAP_MAX_TEXT_CHARS]
    except Exception:
        return None


def _pdf_text(payload: bytes):
    """Extract PDF text, refusing documents above the configured page cap."""
    try:
        reader = pypdf.PdfReader(BytesIO(payload))
        pages = reader.pages
        if len(pages) > config.IMAP_MAX_PDF_PAGES:
            return None
        return "\n".join(
            (page.extract_text() or "") for page in pages
        )[:config.IMAP_MAX_TEXT_CHARS]
    except Exception:
        return None


_INVITE_SUBJECT = "interview invitation"      # every invitation this app sends starts with this


def _core_subject(subject):
    """'RE: Re: Interview Invitation - X' -> 'interview invitation - x'."""
    t = (subject or "").strip().lower()
    while True:
        m = re.match(r"(?:re|aw|sv)\s*:\s*", t)
        if not m:
            return t
        t = t[m.end():]


def handle_confirm(email, state, request: gr.Request = None):
    """Updates the candidate's status to "Confirmed" and shows a success message."""
    if not _authorized(request, "send_invitations"):
        return _denied_html("send_invitations"), state
    email_clean = (email or "").strip().lower()
    if not email_clean:
        return '<div class="status-line err">⚠ No email provided.</div>'
    new_state = dict(state) if isinstance(state, dict) else {"replies_status": {}}
    if "replies_status" not in new_state or not isinstance(new_state.get("replies_status"), dict):
        new_state["replies_status"] = {}
    new_state["replies_status"][email_clean] = "Confirmed"
    _remember_candidate(email_clean, "interview", "candidate confirmed the interview")
    return (f'<div class="status-line ok">✅ { _html.escape(email) } status updated to <b>Confirmed</b>.</div>', new_state)


def handle_reschedule(email, state, request: gr.Request = None):
    """Triggers the email agent to draft a reschedule response."""
    if not _authorized(request, "send_invitations"):
        return _denied_html("send_invitations"), state
    email_clean = (email or "").strip().lower()
    if not email_clean:
        return '<div class="status-line err">⚠ No email provided.</div>'
    new_state = dict(state) if isinstance(state, dict) else {"replies_status": {}}
    if "replies_status" not in new_state or not isinstance(new_state.get("replies_status"), dict):
        new_state["replies_status"] = {}
    new_state["replies_status"][email_clean] = "Reschedule Requested"
    return (f'<div class="status-line info">📅 Reschedule request recorded for { _html.escape(email) }.</div>', new_state)


def handle_decline(email, state, request: gr.Request = None):
    """Marks the candidate as "Declined"."""
    if not _authorized(request, "send_invitations"):
        return _denied_html("send_invitations"), state
    email_clean = (email or "").strip().lower()
    if not email_clean:
        return '<div class="status-line err">⚠ No email provided.</div>'
    new_state = dict(state) if isinstance(state, dict) else {"replies_status": {}}
    if "replies_status" not in new_state or not isinstance(new_state.get("replies_status"), dict):
        new_state["replies_status"] = {}
    new_state["replies_status"][email_clean] = "Declined"
    _remember_candidate(email_clean, "reject", "candidate declined the interview")
    return (f'<div class="status-line ok">❌ { _html.escape(email) } marked as <b>Declined</b>.</div>', new_state)


def _scan_replies(emails_state=None):
    """Return ``(status_html, reply_dicts)`` for replies to our invitations."""
    candidate_addrs = {str(e.get("to", "")).lower() for e in (emails_state or [])
                       if isinstance(e, dict)}
    replies = []
    try:
        client = _gmail_client()
        ids = client.list_message_ids(limit=config.IMAP_SCAN_LIMIT)
    except (gmail_api.GmailAPIError, google_oauth.OAuthError) as ex:
        return (f'<div class="status-line err">❌ Gmail: {_error_html(ex)}. '
                'Reconnect Google OAuth in the Gmail inbox section.</div>', [])

    for msg_id in ids:
        try:
            raw = client.get_raw(msg_id)
        except (gmail_api.GmailAPIError, google_oauth.OAuthError):
            continue
        if not raw:
            continue
        msg = email_lib.message_from_bytes(raw)
        subject = _decode_mime_header(msg.get("Subject", "(no subject)"))
        from_addr = email.utils.parseaddr(msg.get("From", ""))[1].lower()
        is_reply_msg = (subject.strip().lower().startswith(("re:", "aw:", "sv:"))
                        or msg.get("In-Reply-To") is not None
                        or msg.get("References") is not None)
        replies_to_invitation = is_reply_msg and _core_subject(subject).startswith(_INVITE_SUBJECT)
        from_invited = from_addr in candidate_addrs
        if not (replies_to_invitation or from_invited):
            continue

        body = _extract_plain_body(msg).strip()
        looks_like_application = from_invited and (
            _find_attachment_name(msg) is not None
            or any(k in f"{subject} {body}".lower() for k in _APPLICATION_KEYWORDS))

        if not (replies_to_invitation or (from_invited and not looks_like_application)):
            continue
        replies.append({
            "from": from_addr,
            "subject": subject,
            "date": msg.get("Date", ""),
            "body": body[:600],
            "invited": from_invited,
        })

    if not replies:
        return '<div class="empty-state">No replies to your interview invitations found yet.</div>', []

    cards = []
    for r in replies:
        badge = ('<span class="attach-chip">✓ invited this session</span>'
                 if r["invited"] else "")
        # Same deterministic triage used by the reply_triage tool. It only
        # suggests an intent; the recruiter still makes the decision.
        triage = guardrails_mod.classify_reply(r["body"])
        review_badge = (
            '<span class="chip review" style="margin-left:6px;">Review</span>'
            if triage.requires_human else ""
        )

        cards.append(f"""
        <div class="reply-card">
          <span class="reply-date">{_html.escape(str(r['date']))}</span>
          <div class="email-to">{_html.escape(str(r['from']))} {badge}</div>
          <div class="email-subject">✉ {_html.escape(str(r['subject']))}</div>
          <div class="email-body">{_html.escape(str(r['body']))}</div>
          <div style="margin-top:12px; font-size:0.75rem; color:#a5b4fc;">
            Suggested intent: {_html.escape(triage.intent)} · {triage.confidence:.0%} confidence {review_badge}
          </div>
        </div>""")
    return f'<div class="reply-grid">{"".join(cards)}</div>', replies


def check_replies(emails_state=None, request: gr.Request = None):
    """Show only replies to the interview invitations this app sent."""
    if not _authorized(request, "read_resume"):
        return _denied_html("read_resume")
    status_html, _replies = _scan_replies(emails_state)
    return status_html


def check_replies_ui(emails_state=None, request: gr.Request = None):
    """UI variant that also populates the real Gradio reply-picker control."""
    if not _authorized(request, "read_resume"):
        return _denied_html("read_resume"), gr.update(choices=[], value=None)
    status_html, replies = _scan_replies(emails_state)
    choices = []
    for r in replies:
        label = f"{r['from']} — {r['subject'][:60]}"
        if (label, r["from"]) not in choices:
            choices.append((label, r["from"]))
    return status_html, gr.update(choices=choices, value=choices[0][1] if choices else None)


def _oauth_status_html(verify: bool = False) -> str:
    """Describe the local OAuth state without exposing tokens."""
    try:
        status = google_oauth.default_oauth().status()
    except Exception as ex:  # noqa: BLE001 - configuration must never crash the page
        return f'<div class="status-line err">❌ Google OAuth configuration error: {_error_html(ex)}</div>'
    if not status.get("configured"):
        return ('<div class="status-line info">ℹ Google OAuth is not configured. Set '
                '<code>GOOGLE_OAUTH_CLIENT_ID</code> and '
                '<code>GOOGLE_OAUTH_CLIENT_SECRET</code>, then click Connect.</div>')
    if not status.get("token_present"):
        return ('<div class="status-line info">🔑 OAuth client configured, but no token yet. '
                'Click <b>Connect Google account</b> (local) or paste a refresh token below '
                '(deployed).</div>')
    if not verify:
        return ('<div class="status-line ok">✅ Google OAuth token is saved. '
                'Use <b>Check connection</b> to verify Gmail access.</div>')
    try:
        profile = gmail_api.GmailClient().profile()
        email = _html.escape(str(profile.get("emailAddress", "")))
        return f'<div class="status-line ok">✅ Connected to Gmail as <b>{email}</b>.</div>'
    except (gmail_api.GmailAPIError, google_oauth.OAuthError) as ex:
        return f'<div class="status-line err">❌ Gmail connection failed: {_error_html(ex)}</div>'
    except Exception as ex:  # noqa: BLE001 - sanitized for display
        return f'<div class="status-line err">❌ Gmail connection failed: {_error_html(ex)}</div>'


def connect_google_oauth():
    """Run the one-time localhost OAuth consent flow."""
    try:
        path = google_oauth.run_local_flow(open_browser=True, timeout_seconds=300)
        return (f'<div class="status-line ok">✅ Google account connected. '
                f'Token saved to <code>{_html.escape(path)}</code>.</div>')
    except google_oauth.OAuthError as ex:
        return f'<div class="status-line err">❌ OAuth failed: {_error_html(ex)}</div>'
    except Exception as ex:  # noqa: BLE001 - sanitized for display
        return f'<div class="status-line err">❌ OAuth failed: {_error_html(ex)}</div>'


def save_google_refresh_token(refresh_token):
    """Persist a deployment-provided refresh token (never echoed back)."""
    token = (refresh_token or "").strip()
    if not token:
        return '<div class="status-line err">⚠ Paste a refresh token first.</div>'
    try:
        oauth = google_oauth.default_oauth()
        google_oauth.save_token(
            google_oauth.OAuthToken(access_token="", refresh_token=token, expires_at=0),
            oauth.config.token_file,
        )
        oauth.reload()
        return ('<div class="status-line ok">✅ Refresh token saved. '
                'Use <b>Check connection</b> to verify Gmail access.</div>')
    except Exception as ex:  # noqa: BLE001 - never echo the token
        return f'<div class="status-line err">❌ Could not save the token: {_error_html(ex)}</div>'


def _job_workspace() -> workspace_mod.JobWorkspace:
    return workspace_mod.JobWorkspace(config.WORKSPACE_FILE)


def _workspace_choices():
    try:
        jobs = _job_workspace().list_jobs()
    except workspace_mod.WorkspaceError:
        return []
    return [
        (f"{job['title']} — {job['company']}" if job.get("company") else job["title"], job["id"])
        for job in jobs
    ]


def _workspace_update(message, selected=""):
    return gr.update(choices=_workspace_choices(), value=selected or None), message


def load_workspace_job(job_id):
    """Load a saved job into the hiring form."""
    if not job_id:
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                '<div class="status-line err">⚠ Select a saved job first.</div>')
    try:
        job = _job_workspace().get(job_id)
    except workspace_mod.WorkspaceError as ex:
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                f'<div class="status-line err">❌ {_html.escape(str(ex))}</div>')
    if not job:
        return (gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                '<div class="status-line err">⚠ That job no longer exists.</div>')
    _job_workspace().set_active(job["id"])
    return (
        job["title"],
        job["company"],
        ", ".join(job["required_skills"]),
        ", ".join(job["nice_to_have"]),
        job["min_years"],
        f'<div class="status-line ok">✅ Loaded workspace job “{_html.escape(job["title"])}”.</div>',
    )


def save_workspace_job(job_id, position, company, skills_text, nice_text, min_years,
                       invite_body, new_job=False):
    """Create a new job or update the selected one."""
    if not (position or "").strip():
        return _workspace_update(
            '<div class="status-line err">⚠ Enter a position / job title first.</div>',
            job_id,
        )
    try:
        workspace = _job_workspace()
        fields = {
            "title": position,
            "company": company,
            "required_skills": _parse_skill_list(skills_text),
            "nice_to_have": _parse_skill_list(nice_text),
            "min_years": min_years,
            "invite_template": invite_body or "",
        }
        if job_id and not new_job:
            job = workspace.update(job_id, **fields)
            workspace.set_active(job["id"])
            action = "updated"
        else:
            job = workspace.create(**fields)
            action = "created"
    except workspace_mod.WorkspaceError as ex:
        return _workspace_update(
            f'<div class="status-line err">❌ {_html.escape(str(ex))}</div>', job_id)
    return _workspace_update(
        f'<div class="status-line ok">✅ Workspace job “{_html.escape(job["title"])}” '
        f'{action}.</div>',
        job["id"],
    )


def delete_workspace_job(job_id):
    if not job_id:
        return _workspace_update(
            '<div class="status-line err">⚠ Select a saved job first.</div>')
    try:
        removed = _job_workspace().delete(job_id)
    except workspace_mod.WorkspaceError as ex:
        return _workspace_update(f'<div class="status-line err">❌ {_html.escape(str(ex))}</div>')
    if not removed:
        return _workspace_update('<div class="status-line err">⚠ That job no longer exists.</div>')
    return _workspace_update('<div class="status-line info">🗑 Workspace job deleted.</div>')


def render_score_cards_from_state(inbox_state):
    return scorecard.render_score_cards((inbox_state or {}).get("candidates", []))


def export_candidates_csv(inbox_state, request: gr.Request = None):
    """Write the current ranked candidates to a CSV temp file."""
    if not _authorized(request, "read_resume"):
        return None, _denied_html("read_resume")
    candidates = (inbox_state or {}).get("candidates", [])
    if not candidates:
        return None, '<div class="status-line err">⚠ Scan the inbox first — no candidates to export.</div>'
    path = runtime.new_temp_file(suffix=".csv")
    try:
        exporters.write_candidates_csv(candidates, path)
        return path, (f'<div class="status-line ok">✅ Exported {len(candidates)} candidate(s) '
                      'as CSV.</div>')
    except Exception as ex:  # noqa: BLE001 - never expose raw exception text
        return None, f'<div class="status-line err">❌ CSV export failed: {_error_html(ex)}</div>'


def _interview_slot_plan(n):
    """Calendar-aware slots when OAuth is connected, otherwise generic slots."""
    try:
        client = calendar_api.CalendarClient()
        slots = client.find_available_slots(
            n,
            slot_minutes=config.INTERVIEW_SLOT_MINUTES,
        )
        return [(skills_mod.format_interview_slot(slot), slot) for slot in slots], ""
    except (calendar_api.CalendarAPIError, google_oauth.OAuthError) as ex:
        note = (f'Calendar free/busy unavailable ({type(ex).__name__}); '
                'generic business-hour slots were used.')
        datetimes = skills_mod.suggest_interview_slot_datetimes(n)
        return [(skills_mod.format_interview_slot(slot), slot) for slot in datetimes], note


def _ics_filename(candidate_name, position):
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", f"interview-{candidate_name}-{position}").strip("-")
    return (base or "interview")[:80] + ".ics"


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
            filename = os.path.basename(str(part.get_filename() or ""))[:255]
            if filename.lower().endswith((".pdf", ".doc", ".docx", ".rtf", ".txt")):
                return filename
    return None


def _extract_attachment_data(msg):
    """Return ``(filename, text, hidden_report)`` for the first resume attachment."""
    if not msg.is_multipart():
        return None, "", None
    for part in msg.walk():
        cd = part.get("Content-Disposition", "") or ""
        if "attachment" not in cd.lower():
            continue
        filename = os.path.basename(str(part.get_filename() or ""))[:255]
        low = filename.lower()
        if not low.endswith((".pdf", ".docx", ".txt", ".rtf")):
            # legacy binary .doc needs extra libraries; fall back to the email body instead.
            continue
        payload = _attachment_payload(part)
        if not payload:
            continue
        hidden = None
        if low.endswith(".docx"):
            text = _docx_text(payload)
        elif low.endswith(".pdf"):
            text = _pdf_text(payload)
            try:
                hidden = extractor_mod.detect_hidden_pdf_text(payload)
            except Exception:
                hidden = None
        else:
            try:
                text = payload.decode("utf-8", errors="ignore")[:config.IMAP_MAX_TEXT_CHARS]
            except Exception:
                text = None
        if text is not None:
            return filename, text, hidden
    return None, "", None


def _extract_attachment_text(msg):
    """Return (filename, extracted_text) for the first resume-like attachment."""
    filename, text, _hidden = _extract_attachment_data(msg)
    return filename, text


def render_application_email_cards(candidates):
    if not candidates:
        return ""
    cards = []
    for c in candidates:
        att = (f'<span class="attach-chip">📎 {_html.escape(c["_attachment"])}</span>'
               if c.get("_attachment") else "")
        cls, label = REC_CHIP.get(c["recommendation"], ("review", c["recommendation"]))
        label = _html.escape(str(label))
        body_text = c.get("_body", "") or "(no body content found)"
        conf = c.get("confidence", c.get("_confidence", 0))
        mode = _html.escape(str(c.get("extraction_mode", "deterministic")))
        hint = c.get("memory_hint")
        hint_html = (f'<div style="margin-top:6px; font-size:0.8rem; color:#c4b5fd;">'
                     f'🧠 {_html.escape(str(hint))}</div>' if hint else "")
        review_html = ('<span class="chip review">Human review</span>'
                       if c.get("needs_human_review") else "")
        cards.append(f"""
        <div class="reply-card">
          <span class="reply-date">{_html.escape(c.get('_date', ''))}</span>
          <div class="email-to">{_html.escape(c['name'])}</div>
          <div class="email-addr">{_html.escape(c['email'])}</div>
          <div class="email-subject">✉ {_html.escape(c.get('_subject', '') or '(no subject)')} {att}</div>
          <div class="email-body" style="max-height:260px; overflow-y:auto;">{_html.escape(body_text)}</div>
          <div style="margin-top:10px;">
            <span class="chip {cls}">{label}</span>
            <span class="match-pct" style="margin-left:8px;">{c['match_percent']}% match · {conf}% conf · {mode}</span>
            {review_html}
          </div>
          {hint_html}
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


_POSITION_STOPWORDS = {
    "position", "role", "job", "title", "the", "a", "an", "of", "for", "and", "at", "in", "to",
    "we", "are", "is", "hiring", "looking", "opening", "vacancy", "senior", "junior", "lead",
    "sr", "jr", "i", "ii", "iii", "iv", "intern", "internship", "opportunity",
}


# FALLBACK ONLY
def _position_words(position):
    """'Senior Software Engineer' -> ['software', 'engineer'] — the words that actually
    identify the role, stripped of seniority/filler words that would over-restrict matching."""
    words = re.findall(r"[a-z0-9]+", (position or "").lower())
    return [w for w in words if w not in _POSITION_STOPWORDS and len(w) > 1]


_AMBIGUOUS_SKILLS = {"go", "r", "c"}

# Microsoft Office apps whose names are also ordinary English words/verbs
# ("...aims to excel in...", "...access to...", "...as part of a project...",
# "...to word the summary...", "...works well in teams...") — these need a
# much stricter context than a plain word match, or almost every resume
# "matches" them regardless of actual skill.
_OFFICE_AMBIGUOUS = {"excel", "word", "access", "project", "teams", "outlook", "forms", "planner"}


# FALLBACK ONLY
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

    if skill in _OFFICE_AMBIGUOUS:
        # Only count it after "Microsoft"/"MS" (e.g. "Microsoft Excel"), or when it
        # sits in a skills list (immediately followed by a delimiter or end of text) —
        # never on its own as a plain English word/verb.
        esc = re.escape(skill)
        pattern = (r"(?<![a-z0-9])(?:microsoft|ms)\s+" + esc + r"(?![a-z0-9])"
                   r"|(?<![a-z0-9])" + esc + r"(?=\s*(?:[,;/|•·)]|$))")
        return re.search(pattern, text_norm) is not None

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


def _estimate_experience_years(text):
    return skills_mod.estimate_experience_years(text)


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
        "required_skill_groups": [],
        "nice_to_have": nice,
        "min_years": min_years,
        "seniority": "senior" if min_years >= 5 else ("mid" if min_years >= 2 else "junior"),
        "qualifications": [],
    }


DEFAULT_INVITE_TEMPLATE = (
    "Dear {Candidate Name},\n\n"
    "We are pleased to inform you that you have been shortlisted for an interview for the "
    "position of **{Job Title}** at **{Company Name}**.\n\n"
    "Please find the interview details below:\n\n"
    "**Date:** [Date]\n"
    "**Time:** [Time]\n"
    "**Location:** [Location], {Company Name}\n"
    "**Interview Mode:** [Mode]\n\n"
    "Kindly confirm your availability by replying to this email. Please arrive a few minutes "
    "early. We also recommend bringing an updated copy of your CV.\n\n"
    "We look forward to meeting you and discussing your qualifications, skills, and potential "
    "contribution to our team.\n\n"
    "Best regards,\n**{Sender Name}**\n{Company Name}\n{Official Email Address}"
)

CUSTOM_INVITE_SAMPLE = DEFAULT_INVITE_TEMPLATE
_SAMPLE_BLANKS = ("[Date]", "[Time]", "[Location]", "[Mode]")

# {Placeholders} are matched case-insensitively; spaces / "_" / "-" are interchangeable.
_PLACEHOLDER_RE = re.compile(r"\{\s*([A-Za-z][A-Za-z0-9 _\-]*?)\s*\}")
_PLACEHOLDER_ALIASES = {
    "candidate name": "candidate_name", "full name": "candidate_name", "candidate": "candidate_name",
    "first name": "first_name", "name": "first_name",
    "job title": "position", "position": "position", "role": "position", "job": "position",
    "company name": "company", "company": "company",
    "skills": "skills", "matched skills": "skills",
    "slot": "slot", "interview slot": "slot",
    "sender name": "recruiter", "recruiter": "recruiter", "recruiter name": "recruiter",
    "official email address": "sender_email", "official email": "sender_email",
    "sender email": "sender_email", "email address": "sender_email",
}
PLACEHOLDERS_HELP = ("{Candidate Name}, {First Name}, {Job Title}, {Company Name}, {Sender Name}, "
                     "{Official Email Address}, {Skills}, {Slot}")


def _norm_placeholder(raw):
    return re.sub(r"[\s_\-]+", " ", raw.strip().lower())


def _unknown_placeholders(template):
    """{Word} tokens in the HR's text that are not a supported placeholder (typos etc.)."""
    out = []
    for raw in _PLACEHOLDER_RE.findall(template or ""):
        if _norm_placeholder(raw) not in _PLACEHOLDER_ALIASES and raw not in out:
            out.append(raw)
    return out


def _fill_invite_template(template, values):
    def repl(m):
        key = _PLACEHOLDER_ALIASES.get(_norm_placeholder(m.group(1)))
        if key is None:
            return m.group(0)
        return str(values.get(key, "")).replace("**", "")   # values must never inject bold markers
    out = _PLACEHOLDER_RE.sub(repl, template)
    # "Acme Ltd." followed by the template's own "." would print "Acme Ltd.." — collapse it
    # (a real "..." ellipsis is left alone).
    return re.sub(r"(?<!\.)\.\.(?!\.)", ".", out)


def _bold_inline(text):
    """HTML-escape `text`, then turn **bold** into <strong>bold</strong> (newlines untouched)."""
    return re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", _html.escape(text))


def _email_html(text_md):
    """HTML version of the email for Gmail & co.: paragraphs, line breaks, **bold**."""
    paras = re.split(r"\n\s*\n", text_md.strip())
    out = []
    for para in paras:
        lines = [_bold_inline(line.rstrip()) for line in para.split("\n")]
        out.append('<p style="margin:0 0 14px 0;">' + "<br>".join(lines) + "</p>")
    return ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
            'line-height:1.55;color:#222;">' + "".join(out) + "</div>")


def _check_invite_template(mode, template):
    """Problem with the custom text (as a plain-text message), or None if it is fine."""
    if mode != "custom":
        return None
    t = (template or "").strip()
    if not t:
        return "your custom email text is empty — write your email, or switch to “Use the default text”."
    bad = _unknown_placeholders(t)
    if bad:
        shown = ", ".join("{" + b + "}" for b in bad)
        return f"unknown placeholder(s) {shown}. Allowed: {PLACEHOLDERS_HELP}."
    left = [b for b in _SAMPLE_BLANKS if b in t]
    if left:
        return f"{', '.join(left)} are still the sample blanks — replace them with the real interview details."
    return None


def save_default_text(custom_text):
    """Make the HR's custom text their default. It is kept in THIS browser's localStorage
    (gr.BrowserState) — never on the server — so every HR has their own default."""
    problem = _check_invite_template("custom", custom_text)
    if problem:
        return (gr.skip(), gr.skip(),
                f'<div class="status-line err">⚠ Not saved as default — {_html.escape(problem)}</div>')
    text = custom_text.replace("\r\n", "\n").strip()
    return (text, text,
            '<div class="status-line ok">✅ Saved. “Use the default text” now sends this text. '
            'It is remembered in this browser only.</div>')


def restore_default_text():
    return ("", DEFAULT_INVITE_TEMPLATE,
            '<div class="status-line info">↺ Default text is back to the original.</div>')


def load_saved_default(saved):
    """On page load: the saved default if there is a valid one, else the built-in text."""
    if isinstance(saved, str) and saved.strip() and _check_invite_template("custom", saved) is None:
        return saved
    return DEFAULT_INVITE_TEMPLATE


def _draft_invitation(candidate, position, company, recruiter, slot, body_template=None, sender_email=""):
    """Whole email (greeting → signature) from the default template, or from the HR's own
    `body_template`. {Placeholders} are filled from the form; **text** becomes bold in the
    HTML part that Gmail shows (the plain-text part just drops the asterisks)."""
    full = (candidate.get("name") or "there").strip()
    first = full.split()[0]
    strengths = candidate.get("matched_required") or candidate.get("skills") or []
    company = (company or "").strip() or "our company"
    recruiter = (recruiter or "").strip() or "HR Team"
    slot = re.sub(r"\s+[A-Z][A-Z0-9:+-]*$", "", slot or "").strip()

    template = (body_template or "").replace("\r\n", "\n").strip() or DEFAULT_INVITE_TEMPLATE
    filled = _fill_invite_template(template, {
        "candidate_name": full, "first_name": first, "position": position, "company": company,
        "skills": ", ".join(strengths[:3]) if strengths else "your background",
        "slot": slot, "recruiter": recruiter, "sender_email": (sender_email or "").strip(),
    })
    body_md = "\n".join(line.rstrip() for line in filled.split("\n"))
    return {
        "to": candidate.get("email", ""),
        "candidate": candidate.get("name", ""),
        "subject": f"Interview Invitation - {position} at {company}",
        "body": body_md.replace("**", ""),     # plain-text part
        "body_md": body_md,                    # with **markers**, for the in-app card
        "html": _email_html(body_md),          # HTML part (bold etc.)
        "slot": slot,
    }


def _trace_entries(trace_state, run_context):
    """Return this session's traces; never a process-wide singleton."""
    if run_context is not None:
        try:
            return run_context.trace.get_traces(limit=1000)
        except Exception:
            return []
    return list(trace_state or [])


def update_trace_table(trace_state, run_context, request: gr.Request = None):
    """Update trace table display from this session's trace state."""
    if request is not None and _identity_from_request(request)[0].role != Role.ADMIN:
        return []
    traces = _trace_entries(trace_state, run_context)
    if not traces:
        return []

    rows = []
    for t in traces[-50:]:  # Show last 50
        rows.append([
            t.get("agent", ""),
            t.get("action", ""),
            t.get("tool", ""),
            t.get("input_summary", "")[:80],
            t.get("output_summary", "")[:80],
            t.get("duration_ms", 0),
            t.get("timestamp", ""),
        ])
    return rows


def download_trace_json(trace_state, run_context, request: gr.Request = None):
    """Download this session's trace as a JSON file (temp file tracked for cleanup)."""
    if request is not None and _identity_from_request(request)[0].role != Role.ADMIN:
        return None
    data = _trace_entries(trace_state, run_context)
    path = runtime.new_temp_file(suffix=".json")
    try:
        if run_context is not None:
            payload = {
                "run_id": run_context.trace.run_id,
                "budget": run_context.budget.snapshot(),
                "traces": data,
            }
        else:
            payload = {"traces": data}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        return path
    except Exception as e:  # noqa: BLE001 - never expose raw exception text
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"error": safe_error(e)}, handle, indent=2)
        except OSError:
            pass
        return path


def _recommendation_for(match):
    return skills_mod.recommendation_for_score(match)


def analyze_inbox_applications(position, skills_text, nice_text, min_years,
                               use_coordinator=False, blind_mode=False,
                               request: gr.Request = None, limit=60):
    """Scan Gmail (OAuth) and run every candidate through the Phase 3 blocks.

    The LLM is confined to quote-verified extraction. Scoring, review flags,
    ranking and drafting are deterministic. If the ADK coordinator is enabled
    and available it runs over the already-quarantined profiles; any failure
    falls back to deterministic results with a visible banner.
    """
    empty_state = {"title": "", "candidates": []}
    empty_stats = _stats_html_for([])

    def _fail(html_msg):
        return (empty_stats, html_msg, render_candidate_table([]), make_score_chart([]),
                make_skills_chart([]), gr.update(choices=[], value=[]), empty_state, "")

    if not _authorized(request, "read_resume"):
        return _fail(_denied_html("read_resume"))
    if not (position or "").strip():
        return _fail('<div class="status-line err">⚠ Enter the position you are hiring for.</div>')
    if not _parse_skill_list(skills_text):
        return _fail('<div class="status-line err">⚠ Enter at least one required skill (comma separated).</div>')

    requirements = build_custom_requirements(position, skills_text, nice_text, min_years)
    wanted = requirements["required_skills"] + requirements["nice_to_have"]
    try:
        scan_limit = max(1, min(int(limit or 60), config.IMAP_SCAN_LIMIT))
    except (TypeError, ValueError):
        scan_limit = min(60, config.IMAP_SCAN_LIMIT)

    candidates = []
    total_found = 0
    resume_texts = {}
    validated_profiles = {}
    hidden_reports = {}
    coordinator_note = ""
    gmail_error = ""
    model_state = phase3_runner.model_status()
    llm_enabled = bool(model_state.agentic)
    selected_model = model_state.model or config.get_gemini_model()
    memory = _candidate_memory()
    run_ctx = runtime.new_run_context(max_steps=scan_limit * 3 + 15)
    try:
        client = _gmail_client()
        with runtime.QUEUE_GATE.slot(timeout=30):
            position_words = _position_words(requirements["title"])
            ids = client.list_message_ids(limit=scan_limit)
            for msg_id in ids:
                try:
                    raw = client.get_raw(msg_id)
                except (gmail_api.GmailAPIError, google_oauth.OAuthError) as ex:
                    gmail_error = safe_error(ex)
                    run_ctx.budget.consume()
                    continue
                if not raw:
                    run_ctx.budget.consume()
                    continue
                msg = email_lib.message_from_bytes(raw)
                subject = _decode_mime_header(msg.get("Subject", "") or "")
                body = _extract_plain_body(msg)
                attachment_name, attachment_text, hidden_report = _extract_attachment_data(msg)
                blob = f"{subject} {body}".lower()
                has_attachment = _find_attachment_name(msg) is not None
                if not (has_attachment or any(k in blob for k in _APPLICATION_KEYWORDS)):
                    run_ctx.budget.consume()
                    continue
                total_found += 1
                # The position itself must actually be mentioned in the email (subject or
                # body) — e.g. "Software Engineer" applicants only, not every applicant who
                # happens to share a skill or two with the role.
                if position_words and not all(w in blob for w in position_words):
                    run_ctx.budget.consume()
                    continue

                from_name, from_addr = email.utils.parseaddr(msg.get("From", ""))
                from_name = _decode_mime_header(from_name)
                resume_text = attachment_text.strip() if attachment_text.strip() else body
                source_id = (from_addr or f"message-{msg_id}")[:255]

                result = phase3_runner.analyze_resume_text(
                    resume_text,
                    requirements,
                    source_id=source_id,
                    run_context=run_ctx,
                    memory=memory,
                    model=selected_model,
                    llm_enabled=llm_enabled,
                    hidden_report=hidden_report,
                    include_profile=True,
                    blind=bool(blind_mode),
                    known_names=[from_name] if (blind_mode and from_name) else None,
                )
                validated_profile = result.pop("_profile", None)
                if validated_profile is not None:
                    validated_profiles[source_id] = validated_profile
                resume_texts[source_id] = resume_text
                if hidden_report is not None:
                    hidden_reports[source_id] = hidden_report

                if from_name:
                    result["name"] = from_name
                if from_addr:
                    result["email"] = from_addr
                else:
                    result["name"] = result.get("name") or "Unknown Candidate"
                if not requirements["nice_to_have"]:
                    # No nice-to-have list -> drop that 15% component and re-normalise to 100.
                    result["match_percent"] = min(
                        100.0,
                        round(result["match_percent"] / (1 - skills_mod.NICE_TO_HAVE_WEIGHT), 1),
                    )
                    result["recommendation"] = _recommendation_for(result["match_percent"])
                result["_min_years"] = requirements["min_years"]
                result["_subject"] = subject
                result["_attachment"] = attachment_name
                result["_body"] = body.strip()
                result["_date"] = msg.get("Date", "")
                candidates.append(result)
                run_ctx.trace.record("screener", "scored_application", "gmail",
                                     input_summary="1 message",
                                     output_summary=result["recommendation"])
    except runtime.QueueFullError:
        return _fail('<div class="status-line err">⚠ The server is busy. Try again shortly.</div>')
    except (gmail_api.GmailAPIError, google_oauth.OAuthError) as ex:
        return _fail(f'<div class="status-line err">❌ Gmail: {_error_html(ex)}. '
                     'Connect Google OAuth in the inbox section above.</div>')
    except runtime.BudgetExceededError:
        return _fail('<div class="status-line err">⚠ This scan exceeded the per-run budget.</div>')
    except Exception as ex:  # noqa: BLE001 - sanitized for display
        return _fail(f'<div class="status-line err">❌ Error: {_error_html(ex)}</div>')

    if not candidates and gmail_error and not total_found:
        return _fail(f'<div class="status-line err">❌ Gmail: {_html.escape(gmail_error)}. '
                     'Check the OAuth connection.</div>')

    if use_coordinator and candidates:
        if phase3_runner.coordinator_available() and not run_ctx.budget.degraded:
            try:
                identity = _identity_from_request(request)[0]
                coordinator_result = phase3_runner.run_coordinator(
                    requirements=requirements,
                    resume_texts=resume_texts,
                    source_ids=list(resume_texts.keys()),
                    validated_profiles=validated_profiles,
                    hidden_reports=hidden_reports,
                    role=identity.role.value,
                    model=selected_model,
                    run_context=run_ctx,
                    memory=memory,
                    jd_filename="",
                )
                by_source = {c.get("source_id"): c for c in candidates}
                merged = []
                for scored in coordinator_result.get("ranked_candidates", []):
                    base = by_source.get(scored.get("source_id"))
                    if base is None:
                        merged.append(scored)
                        continue
                    meta = {k: v for k, v in base.items() if k.startswith("_")}
                    item = {**scored, **meta}
                    item["name"] = base.get("name", item.get("name"))
                    item["email"] = base.get("email", item.get("email"))
                    item["evidence"] = base.get("evidence", item.get("evidence"))
                    item["_confidence"] = item.get("confidence", item.get("_confidence", 0))
                    if not requirements["nice_to_have"]:
                        item["match_percent"] = min(
                            100.0,
                            round(item["match_percent"] / (1 - skills_mod.NICE_TO_HAVE_WEIGHT), 1),
                        )
                        item["recommendation"] = _recommendation_for(item["match_percent"])
                    merged.append(item)
                if merged:
                    candidates = merged
                    coordinator_note = (
                        f"ADK coordinator reviewed {len(merged)} candidate(s) "
                        f"({coordinator_result.get('run_id', '')[:8]})."
                    )
                else:
                    coordinator_note = "Coordinator returned no usable results; deterministic scoring kept."
            except Exception as exc:  # noqa: BLE001 - always keep deterministic results
                coordinator_note = (
                    f"Coordinator unavailable ({type(exc).__name__}); "
                    "deterministic Phase 3 results kept."
                )
        else:
            coordinator_note = (
                "Coordinator not configured; deterministic Phase 3 results kept."
            )

    unrelated_count = total_found - len(candidates)

    if not candidates:
        if total_found == 0:
            msg = '<div class="empty-state">No candidate application emails detected in this inbox.</div>'
        else:
            msg = (f'<div class="status-line err">⚠ Found {total_found} application email(s) in the inbox, but none '
                   f'mention "{_html.escape(requirements["title"])}" — they look like applications for a '
                   f'different role.</div>')
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
    excluded_note = (f' · {unrelated_count} application(s) for a different role excluded'
                      if unrelated_count else '')
    review_count = sum(1 for c in candidates if c.get("needs_human_review"))
    hidden_count = sum(1 for c in candidates if c.get("hidden_text_suspected"))
    budget_line = phase3_runner.summarize_budget(run_ctx)
    mode_note = (
        f"Mode: {model_state.mode} ({_html.escape(model_state.message)})"
        if model_state.message else f"Mode: {model_state.mode}"
    )
    coordinator_html = (
        f'<div class="status-line info">🤖 {_html.escape(coordinator_note)}</div>'
        if coordinator_note else ""
    )
    blind_html = (
        '<div class="status-line info">🕶 Blind mode: candidate names and contact '
        'details were masked before extraction and scoring.</div>'
        if blind_mode else ""
    )
    review_html = (
        f'<div class="status-line info">🧑‍⚖️ {review_count} candidate(s) need human review · '
        f'{hidden_count} hidden-text suspect(s).</div>'
        if (review_count or hidden_count) else ""
    )
    status = (f'<div class="status-line ok">✅ Found {len(candidates)} relevant application(s), scored for '
              f'“{_html.escape(requirements["title"])}” · required: {_html.escape(req_txt)} · '
              f'min. experience: {requirements["min_years"]} yr(s){excluded_note}</div>'
              f'<div class="status-line info">Shortlist ≥ {config.SHORTLIST_THRESHOLD:.0f}% · Review {config.REVIEW_THRESHOLD:.0f}–{config.SHORTLIST_THRESHOLD - 1:.0f}% · Reject &lt; {config.REVIEW_THRESHOLD:.0f}%</div>'
              f'<div class="status-line info">{mode_note}</div>'
              f'<div class="status-line info">💸 {_html.escape(budget_line)}</div>'
              f'{blind_html}{coordinator_html}{review_html}')
    state = {"title": requirements["title"], "candidates": candidates}
    return (_stats_html_for(candidates), status, render_candidate_table(candidates),
            make_score_chart(candidates), make_skills_chart(coverage),
            gr.update(choices=choice_tuples, value=[]), state, render_application_email_cards(candidates))


def send_invitations_to_selected(sender_email, sender_name, brevo_api_key, company, invite_mode, invite_body,
                                 default_text, selected_emails, inbox_state, sent_invitations_state,
                                 request: gr.Request = None):
    """Deprecated direct-send path; now refuses to send without explicit approval."""
    if not _authorized(request, "send_invitations"):
        return (_denied_html("send_invitations"), sent_invitations_state or [],
                render_emails(sent_invitations_state or []))
    if not selected_emails:
        return ('<div class="status-line err">⚠ Select at least one candidate first.</div>',
                sent_invitations_state or [], render_emails(sent_invitations_state or []))
    queue, problem, calendar_note = _stage_selected_drafts(
        sender_email, sender_name, company, invite_mode, invite_body,
        default_text, selected_emails, inbox_state)
    if problem:
        return (f'<div class="status-line err">⚠ Nothing staged — {_html.escape(problem)}</div>',
                sent_invitations_state or [], render_emails(sent_invitations_state or []))
    note_html = (f'<div class="status-line info">📅 {_html.escape(calendar_note)}</div>'
                 if calendar_note else "")
    return (f'<div class="status-line info">📝 {len(queue.list())} draft(s) staged with '
            '.ics calendar attachments. No email was sent; use Preview Invitations for Approval '
            f'and then Approve &amp; Send.</div>{note_html}',
            sent_invitations_state or [], render_emails(sent_invitations_state or []))


def _stage_selected_drafts(sender_email, sender_name, company, invite_mode, invite_body,
                           default_text, selected_emails, inbox_state):
    """Build draft-only invitations and put them in the human approval queue."""
    if invite_mode == "custom":
        template = invite_body
    else:
        template = (default_text or "").strip() or DEFAULT_INVITE_TEMPLATE
    problem = _check_invite_template("custom", template)
    if problem:
        return None, f"your email text has a problem — {problem}", ""
    candidates = (inbox_state or {}).get("candidates", [])
    title = (inbox_state or {}).get("title") or "the role"
    by_email = {c["email"]: c for c in candidates}
    slots, calendar_note = _interview_slot_plan(len(selected_emails))
    drafts = []
    for i, addr in enumerate(selected_emails):
        candidate = by_email.get(addr)
        if not candidate:
            continue
        slot_label, slot_start = slots[i] if i < len(slots) else ("", None)
        draft = _draft_invitation(
            candidate, title, company, sender_name, slot_label, template, sender_email)
        if slot_start is not None:
            draft["ics_content"] = calendar_api.generate_interview_ics(
                candidate_name=str(candidate.get("name", "") or ""),
                candidate_email=str(candidate.get("email", "") or ""),
                position=title,
                company=str(company or ""),
                start=slot_start,
                duration_minutes=config.INTERVIEW_SLOT_MINUTES,
                location=str(company or ""),
                description=draft.get("body", ""),
                organizer_email=str(sender_email or ""),
                organizer_name=str(sender_name or "HR Team"),
            )
            draft["ics_filename"] = _ics_filename(
                str(candidate.get("name", "") or "candidate"), title)
        drafts.append(draft)
    if not drafts:
        return None, "could not match the selected candidates — scan the inbox again.", ""

    queue = ApprovalQueue()
    queue.stage_many(drafts)
    return queue, None, calendar_note


def stage_invitations_for_approval(sender_email, sender_name, company, invite_mode,
                                   invite_body, default_text, selected_emails,
                                   inbox_state, request: gr.Request = None):
    """First click: preview drafts. Nothing is sent until explicit approval."""
    if not _authorized(request, "send_invitations"):
        return (_denied_html("send_invitations"), [], "")
    if not selected_emails:
        return ('<div class="status-line err">⚠ Select at least one candidate first.</div>',
                [], "")
    queue, problem, calendar_note = _stage_selected_drafts(
        sender_email, sender_name, company, invite_mode, invite_body,
        default_text, selected_emails, inbox_state)
    if problem:
        return (f'<div class="status-line err">⚠ Nothing staged — {_html.escape(problem)}</div>',
                [], "")
    pending = [item.as_dict() for item in queue.list()]
    note_html = (f'<div class="status-line info">📅 {_html.escape(calendar_note)}</div>'
                 if calendar_note else "")
    status = (f'<div class="status-line info">📝 {len(pending)} draft(s) ready with .ics '
              'calendar attachments. Review the email content below, then click '
              '<b>Approve &amp; send</b>. No email has been sent yet.</div>' + note_html)
    return status, pending, queue.preview_html()


def approve_and_send_staged(sender_email, sender_name, brevo_api_key, pending_drafts,
                            sent_invitations_state, request: gr.Request = None):
    """Second click: deterministic send of explicitly approved drafts only.

    The UI click is the human approval. Each draft still passes through
    :func:`src.approval.send_approved_draft`, which refuses anything whose
    ``approved`` flag is not true; no LLM can reach the mail transport.
    """
    if not _authorized(request, "send_invitations"):
        return (_denied_html("send_invitations"), pending_drafts or [],
                render_emails(sent_invitations_state or []), "")
    if not pending_drafts:
        return ('<div class="status-line err">⚠ No drafts are waiting for approval.</div>',
                [], render_emails(sent_invitations_state or []), "")

    queue = ApprovalQueue()
    staged = queue.stage_many(pending_drafts)
    statuses = []
    sent_ok = []
    for item in staged:
        queue.approve(item.id)
        try:
            send_status, sent = send_approved_draft(
                queue.get(item.id),
                sender_email=sender_email,
                sender_name=sender_name,
                api_key=brevo_api_key,
                send_func=send_all_emails,
            )
            statuses.append(send_status)
            sent_ok.extend(sent)
            for draft in sent:
                _remember_candidate(draft.get("to", ""), "shortlist",
                                    "interview invitation approved and sent")
        except ApprovalRequiredError:
            statuses.append(
                f'<div class="status-line err">⛔ Refused to send an unapproved draft '
                f'to {_html.escape(str(item.to))}.</div>'
            )
        except Exception as ex:  # noqa: BLE001 - sanitized for display
            statuses.append(
                f'<div class="status-line err">❌ Could not send to '
                f'{_html.escape(str(item.to))}: {_error_html(ex, (brevo_api_key, sender_email))}</div>'
            )

    updated_state = (sent_invitations_state or []) + sent_ok
    return ("".join(statuses) or '<div class="status-line err">⚠ Nothing sent.</div>',
            [], render_emails(updated_state), "")


def discard_staged_drafts(pending_drafts, sent_invitations_state,
                          request: gr.Request = None):
    if not _authorized(request, "send_invitations"):
        return _denied_html("send_invitations"), pending_drafts or [], ""
    return ('<div class="status-line info">Drafts discarded. Nothing was sent.</div>',
            [], render_emails(sent_invitations_state or []))


def forget_candidate_memory(selected_emails, inbox_state, request: gr.Request = None):
    """Delete HMAC-keyed memory for selected candidates and refresh hints."""
    if not _authorized(request, "read_resume"):
        return (_denied_html("read_resume"), inbox_state,
                render_candidate_table((inbox_state or {}).get("candidates", [])))
    memory = _candidate_memory()
    selected = {str(e).strip().lower() for e in (selected_emails or []) if e}
    if memory is None:
        return ('<div class="status-line err">⚠ Memory store is unavailable.</div>',
                inbox_state,
                render_candidate_table((inbox_state or {}).get("candidates", [])))
    removed = 0
    for address in selected:
        try:
            removed += 1 if memory.forget_candidate(address) else 0
        except Exception:
            continue
    state = dict(inbox_state) if isinstance(inbox_state, dict) else {"candidates": []}
    candidates = state.get("candidates", [])
    for candidate in candidates:
        if str(candidate.get("email", "")).lower() in selected:
            candidate["memory_hint"] = None
    state["candidates"] = candidates
    return (
        f'<div class="status-line info">🧠 Forgot {removed} memory record(s); '
        f'scores were not changed.</div>',
        state,
        render_candidate_table(candidates),
    )


def _init_session(request: gr.Request = None):
    """Create one run context per browser session and gate the dev panel."""
    run_context = runtime.new_run_context()
    runtime.cleanup_temp_files()
    try:
        record, _manager = _identity_from_request(request)
    except AccessDeniedError:
        return run_context, gr.update(visible=False), _denied_html()
    is_admin = record.role == Role.ADMIN
    status = phase3_runner.model_status()
    css_class = "err" if status.degraded else "info"
    banner = (
        f'<div class="status-line {css_class}">Signed in as '
        f'<b>{_html.escape(record.username)}</b> · role: {_html.escape(record.role.value)}'
        f' · Phase 3: {_html.escape(status.mode)} — {_html.escape(status.message)}</div>'
        f'<div class="status-line info">Run {run_context.trace.run_id[:8]} · '
        f'budget {run_context.budget.remaining_calls} LLM call(s) · '
        f'{run_context.budget.remaining_tokens} token(s) remaining</div>'
    )
    return run_context, gr.update(visible=is_admin), banner


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
with gr.Blocks(title="Smart HR Recruitment") as demo:
    sent_invitations_state = gr.State([])
    # The HR's own default invitation text lives in THEIR browser (localStorage). storage_key and
    # secret must be fixed: left random they change on every server restart (Render sleeps a lot)
    # and the saved text would become unreadable.
    browser_state_secret = os.environ.get("BROWSER_STATE_SECRET")
    if hasattr(gr, "BrowserState") and browser_state_secret:
        saved_default_state = gr.BrowserState(
            "", storage_key="smart_hr_invite_default_v2", secret=browser_state_secret)
    else:  # no secret configured (or very old Gradio): session-only state
        saved_default_state = gr.State("")
    inbox_state = gr.State({"title": "", "candidates": []})
    pending_drafts_state = gr.State([])
    trace_state = gr.State([])
    run_context_state = gr.State(None)
    overrides_state = gr.State([])
    replies_action_state = gr.State({"replies_status": {}})
    session_banner = gr.HTML()

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
        position_in = gr.Textbox(label="Position / job title", scale=3)
        company_in = gr.Textbox(label="Company name (used in invitation emails)", scale=3)
        min_exp_in = gr.Number(label="Minimum experience (years)", value=0, minimum=0, precision=0, scale=1)
    with gr.Row():
        skills_in = gr.Textbox(label="Required skills (comma separated)", lines=2, scale=3)
        nice_in = gr.Textbox(label="Nice-to-have skills (optional)", lines=2, scale=2)
    with gr.Accordion("✉️ Invitation email — default text or your own", open=False):
        invite_mode_in = gr.Radio(
            choices=[("Use the default text", "default"), ("Use my own custom text", "custom")],
            value="default", label="Which text should the invitation use?",
        )
        with gr.Column(visible=False) as custom_invite_box:
            gr.Markdown(
                "Write the **whole email**, greeting to signature. Put words between `**double asterisks**` "
                "to make them **bold** — they arrive bold in Gmail too.\n\n"
                "These are filled in automatically from the form: `{Candidate Name}` · `{First Name}` · "
                "`{Job Title}` · `{Company Name}` · `{Sender Name}` · `{Official Email Address}` "
                "(your verified Brevo sender email) · `{Skills}` (candidate's top matched skills) · "
                "`{Slot}` (auto-suggested interview time).\n\n"
                "Replace `[Date]`, `[Time]`, `[Location]`, `[Mode]` with the real interview details "
                "(nothing is sent while they are still there)."
            )
            invite_body_in = gr.Textbox(label="Custom email text", value=CUSTOM_INVITE_SAMPLE, lines=20)
            with gr.Row():
                save_default_btn = gr.Button("⭐ Make this my default text", variant="primary", size="sm")
                reset_body_btn = gr.Button("↺ Reset to the sample", size="sm")
        default_save_status = gr.HTML()
        with gr.Accordion("See / change the default text", open=False):
            default_text_box = gr.Textbox(
                label="Default email text (this is what “Use the default text” sends)",
                value=DEFAULT_INVITE_TEMPLATE, lines=14, interactive=False)
            restore_default_btn = gr.Button("↺ Restore the original default text", size="sm")

    with gr.Accordion("💼 Job workspaces — save and switch between multiple roles", open=False):
        gr.Markdown(
            "Save the role details above as a workspace job, then switch between jobs at any "
            "time. Workspaces store job configuration only — never candidate data or credentials."
        )
        with gr.Row():
            workspace_job_in = gr.Dropdown(label="Saved jobs", choices=[], interactive=True, scale=3)
            refresh_workspace_btn = gr.Button("↻ Refresh", size="sm", scale=1)
        with gr.Row():
            load_workspace_btn = gr.Button("📂 Load selected job", size="sm")
            save_workspace_btn = gr.Button("💾 Save / update selected", size="sm")
            save_workspace_new_btn = gr.Button("➕ Save as new job", size="sm")
            delete_workspace_btn = gr.Button("🗑 Delete selected", size="sm")
        workspace_status = gr.HTML()

    with gr.Accordion("🔐 Gmail inbox access — Google OAuth (read-only)", open=False):
        gr.Markdown(
            "Applications and replies are read through the **Gmail API** with a read-only OAuth "
            "token. No Gmail App Password is used or stored.\n\n"
            "**Local:** set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`, then click "
            "**Connect Google account**. **Deployed:** paste a refresh token produced locally and "
            "save it; it is stored only in the git-ignored data directory."
        )
        oauth_status = gr.HTML()
        with gr.Row():
            connect_oauth_btn = gr.Button("🔗 Connect Google account (local)", variant="primary")
            check_oauth_btn = gr.Button("✅ Check connection")
        with gr.Row():
            oauth_refresh_in = gr.Textbox(
                label="Refresh token (deployment fallback)", type="password",
                placeholder="1//...", scale=3)
            save_oauth_btn = gr.Button("💾 Save refresh token", size="sm", scale=1)

    with gr.Accordion("📧 Brevo sender details — used to actually send emails (never saved)", open=False):
        gr.Markdown(
            "Sends via the **Brevo** transactional email API (HTTPS) — works from the deployed "
            "public link too, unlike raw Gmail SMTP which most free hosts block.\n\n"
            "1. In your Brevo dashboard, go to **Senders & IP** (under Settings) and verify "
            "the email address you'll send from — a confirmation link is emailed to it\n"
            "2. Get your key under **Transactional → Settings → API Keys → Generate a new API key**\n"
            "3. ⚠ Brevo also requires **phone verification** on your account before it will send its "
            "first email — check the banner at the top of your Brevo dashboard if sending fails.\n"
            "4. ⚠ **Use this same email as the \"Gmail address\" above** — replies land here, so "
            "Replies checking only works if both fields point at the one inbox."
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
            agentic_mode_in = gr.Checkbox(
                label="Use Phase 3 ADK coordinator (falls back to deterministic if unavailable)",
                value=bool(config.get_gemini_api_key() and config.get_gemini_model()),
            )
            blind_mode_in = gr.Checkbox(
                label="Blind mode (mask names before extraction and scoring)",
                value=bool(config.BLIND_MODE_DEFAULT),
            )
            applications_status = gr.HTML()
            applications_cards_out = gr.HTML()

        with gr.Tab("📊  Dashboard"):
            with gr.Row():
                score_plot = gr.Plot(label="Candidate Match Scores")
                skills_plot = gr.Plot(label="Required-skill coverage (candidates who have each skill)")

        with gr.Tab("🧾  Score Cards"):
            gr.Markdown(
                "Per-candidate explainability: verified evidence quotes and the concrete "
                "reasons a candidate was **not** shortlisted."
            )
            score_cards_out = gr.HTML(
                '<div class="empty-state">No score cards yet — scan the inbox first.</div>')

        with gr.Tab("🏆  Ranked Candidates"):
            table_out = gr.HTML()
            gr.Markdown("**Select candidates to invite for an interview:**")
            candidate_checkbox = gr.CheckboxGroup(choices=[], label="Detected candidates (ranked highest match first)")
            with gr.Row():
                send_selected_btn = gr.Button("📝  Preview Invitations for Approval", variant="primary")
                forget_memory_btn = gr.Button("🧠  Forget selected candidate memory", size="sm")
            send_selected_status = gr.HTML()
            pending_drafts_out = gr.HTML()
            with gr.Row():
                approve_send_btn = gr.Button("✅  Approve & Send", variant="primary")
                discard_drafts_btn = gr.Button("🗑  Discard Drafts", variant="secondary")
            gr.Markdown("**Export:**")
            with gr.Row():
                export_csv_btn = gr.Button("⬇  Export candidates as CSV", variant="secondary")
            csv_status = gr.HTML()
            csv_download = gr.File(label="Download CSV")

        with gr.Tab("✉️  Interview Emails"):
            gr.Markdown(
                "Shows the full content of every interview invitation **actually sent** so far "
                "(from the Ranked Candidates tab's Send button)."
            )
            emails_out = gr.HTML('<div class="empty-state">No invitations sent yet this session.</div>')

        with gr.Tab("📥  Replies"):
            gr.Markdown("Checks the Gmail inbox above and shows **only the replies to your interview invitations** — the rest of the inbox is ignored.")
            refresh_btn = gr.Button("🔄  Check for Replies", variant="primary")
            replies_out = gr.HTML()
            replies_picker = gr.Dropdown(label="Reply to update", choices=[], interactive=True)
            with gr.Row():
                confirm_reply_btn = gr.Button("✅ Confirm", size="sm")
                reschedule_reply_btn = gr.Button("📅 Reschedule", size="sm")
                decline_reply_btn = gr.Button("❌ Decline", size="sm")
            replies_action_status = gr.HTML()

    with gr.Accordion("🔍 Developer Tools (Agent Trace)", open=False,
                      visible=False) as dev_tools:
        gr.Markdown("View agent execution traces for observability. Admin role only.")
        trace_table = gr.Dataframe(
            headers=["Agent", "Action", "Tool", "Input Summary", "Output Summary", "Duration (ms)", "Timestamp"],
            interactive=False,
            wrap=True,
        )
        with gr.Row():
            refresh_trace_btn = gr.Button("Refresh Trace", variant="secondary")
            download_trace_btn = gr.Button("Download trace as JSON", variant="primary")
        trace_download = gr.File(label="Download Trace")

    fetch_apps_btn.click(
        fn=analyze_inbox_applications,
        inputs=[position_in, skills_in, nice_in, min_exp_in, agentic_mode_in, blind_mode_in],
        outputs=[stats_out, applications_status, table_out, score_plot, skills_plot, candidate_checkbox, inbox_state, applications_cards_out],
    )
    inbox_state.change(
        fn=render_score_cards_from_state,
        inputs=[inbox_state],
        outputs=[score_cards_out],
    )
    export_csv_btn.click(
        fn=export_candidates_csv,
        inputs=[inbox_state],
        outputs=[csv_download, csv_status],
    )

    refresh_workspace_btn.click(
        fn=lambda: _workspace_update('<div class="status-line info">↻ Workspace list refreshed.</div>'),
        inputs=None,
        outputs=[workspace_job_in, workspace_status],
    )
    load_workspace_btn.click(
        fn=load_workspace_job,
        inputs=[workspace_job_in],
        outputs=[position_in, company_in, skills_in, nice_in, min_exp_in, workspace_status],
    )
    save_workspace_btn.click(
        fn=lambda job_id, pos, comp, skills, nice, years, body: save_workspace_job(
            job_id, pos, comp, skills, nice, years, body, new_job=False),
        inputs=[workspace_job_in, position_in, company_in, skills_in, nice_in, min_exp_in,
                invite_body_in],
        outputs=[workspace_job_in, workspace_status],
    )
    save_workspace_new_btn.click(
        fn=lambda job_id, pos, comp, skills, nice, years, body: save_workspace_job(
            job_id, pos, comp, skills, nice, years, body, new_job=True),
        inputs=[workspace_job_in, position_in, company_in, skills_in, nice_in, min_exp_in,
                invite_body_in],
        outputs=[workspace_job_in, workspace_status],
    )
    delete_workspace_btn.click(
        fn=delete_workspace_job,
        inputs=[workspace_job_in],
        outputs=[workspace_job_in, workspace_status],
    )

    connect_oauth_btn.click(fn=connect_google_oauth, inputs=None, outputs=[oauth_status])
    check_oauth_btn.click(fn=lambda: _oauth_status_html(verify=True), inputs=None,
                          outputs=[oauth_status])
    save_oauth_btn.click(fn=save_google_refresh_token, inputs=[oauth_refresh_in],
                         outputs=[oauth_status])
    demo.load(fn=_oauth_status_html, inputs=None, outputs=[oauth_status])
    demo.load(fn=lambda: _workspace_update(""), inputs=None,
              outputs=[workspace_job_in, workspace_status])

    reset_body_btn.click(lambda: CUSTOM_INVITE_SAMPLE, inputs=None, outputs=[invite_body_in])
    save_default_btn.click(save_default_text, inputs=[invite_body_in],
                           outputs=[saved_default_state, default_text_box, default_save_status])
    restore_default_btn.click(restore_default_text, inputs=None,
                              outputs=[saved_default_state, default_text_box, default_save_status])
    demo.load(load_saved_default, inputs=[saved_default_state], outputs=[default_text_box])
    demo.load(_init_session, inputs=None,
              outputs=[run_context_state, dev_tools, session_banner])
    invite_mode_in.change(lambda m: gr.update(visible=(m == "custom")),
                          inputs=[invite_mode_in], outputs=[custom_invite_box])

    forget_memory_btn.click(
        fn=forget_candidate_memory,
        inputs=[candidate_checkbox, inbox_state],
        outputs=[send_selected_status, inbox_state, table_out],
    )
    send_selected_btn.click(
        fn=stage_invitations_for_approval,
        inputs=[sender_email_in, sender_name_in, company_in, invite_mode_in, invite_body_in,
                default_text_box, candidate_checkbox, inbox_state],
        outputs=[send_selected_status, pending_drafts_state, pending_drafts_out],
    )
    approve_send_btn.click(
        fn=approve_and_send_staged,
        inputs=[sender_email_in, sender_name_in, brevo_key_in, pending_drafts_state,
                sent_invitations_state],
        outputs=[send_selected_status, pending_drafts_state, emails_out, pending_drafts_out],
    )
    discard_drafts_btn.click(
        fn=discard_staged_drafts,
        inputs=[pending_drafts_state, sent_invitations_state],
        outputs=[send_selected_status, pending_drafts_state, emails_out],
    )

    refresh_btn.click(
        fn=check_replies_ui,
        inputs=[sent_invitations_state],
        outputs=[replies_out, replies_picker],
    )
    confirm_reply_btn.click(
        fn=handle_confirm,
        inputs=[replies_picker, replies_action_state],
        outputs=[replies_action_status, replies_action_state],
    )
    reschedule_reply_btn.click(
        fn=handle_reschedule,
        inputs=[replies_picker, replies_action_state],
        outputs=[replies_action_status, replies_action_state],
    )
    decline_reply_btn.click(
        fn=handle_decline,
        inputs=[replies_picker, replies_action_state],
        outputs=[replies_action_status, replies_action_state],
    )

    refresh_trace_btn.click(
        fn=update_trace_table,
        inputs=[trace_state, run_context_state],
        outputs=[trace_table],
    )
    download_trace_btn.click(
        fn=download_trace_json,
        inputs=[trace_state, run_context_state],
        outputs=[trace_download],
    )
    demo.load(update_trace_table, inputs=[trace_state, run_context_state],
              outputs=[trace_table])

    gr.HTML('<div id="credit-footer">' + creator_credit_html() + '</div>')


def _authenticate(username: str, password: str) -> bool:
    """Gradio login callback backed by PBKDF2 + lockout + audit logging."""
    manager = auth_mod.get_auth_manager()
    if not manager.enabled:
        return True
    return manager.authenticate(username, password).ok


def main() -> None:
    manager = validate_startup()
    port = int(os.environ.get("PORT", 10000))
    # Without configured users, the only safe mode is unauthenticated loopback.
    if manager.enabled:
        server_name = os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0")
        auth_arg = _authenticate
    else:
        server_name = "127.0.0.1"
        auth_arg = None
    demo.queue(
        max_size=config.QUEUE_MAX_SIZE,
        default_concurrency_limit=config.QUEUE_CONCURRENCY_LIMIT,
    )
    demo.launch(
        server_name=server_name,
        server_port=port,
        auth=auth_arg,
        auth_message="Smart HR Recruitment — sign in with your HR_USERS account.",
        max_threads=config.SERVER_MAX_THREADS,
        state_session_capacity=config.STATE_SESSION_CAPACITY,
        theme=THEME,
        css=CSS,
    )


if __name__ == "__main__":
    main()