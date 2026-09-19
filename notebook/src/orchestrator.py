"""
orchestrator.py
===============
Multi-agent orchestration for the Smart HR Recruitment system.

This module threads the four agents together and produces the deliverable
artifacts (ranked candidates, email drafts, insights report). It mirrors the
ADK ``SequentialAgent`` flow in ``agents.py`` but is fully deterministic, so the
capstone notebook reliably runs end-to-end on Kaggle with or without a Gemini
API key.

Shared-state flow (same as ADK ``output_key`` chaining):

    jd_requirements -> ranked_candidates -> interview_emails -> insights_report

Cross-cutting concerns applied at every stage:
    * MCP filesystem server for all file I/O (no raw disk access by agents)
    * Security layer: RBAC authorization + PII masking in logs
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from . import mcp_filesystem_server as fs
from . import skills
from .security import SecurityContext, Role, get_secure_logger, mask_pii

log = get_secure_logger("smart_hr.orchestrator")

SHORTLIST_THRESHOLD = 70.0  # match_percent required to advance to interview


# --------------------------------------------------------------------------- #
# Stage 1 - JD Agent
# --------------------------------------------------------------------------- #
def run_jd_agent(ctx: SecurityContext, jd_filename: str = "") -> Dict[str, Any]:
    log.info("JD Agent: reading job description via MCP filesystem server")
    jd_text = fs.read_job_description(jd_filename)
    requirements = skills.jd_parser_skill(jd_text)
    log.info("JD Agent: extracted role '%s' (%s required skills)",
             requirements["title"], len(requirements["required_skills"]))
    return requirements


# --------------------------------------------------------------------------- #
# Stage 2 - Resume Screener Agent
# --------------------------------------------------------------------------- #
def run_resume_screener(ctx: SecurityContext, requirements: Dict[str, Any]) -> List[Dict[str, Any]]:
    ctx.authorize("read_resume")
    ctx.authorize("score_candidates")

    resume_files = fs.list_resumes()
    log.info("Resume Screener: found %d resumes to screen", len(resume_files))

    scored: List[Dict[str, Any]] = []
    for fname in resume_files:
        raw = fs.read_resume(fname)
        # Logs are automatically PII-masked, but we also mask explicitly here
        # to demonstrate the security requirement at the data layer.
        log.info("Screening resume %s | preview: %s", fname, mask_pii(raw[:60]))
        profile = skills.resume_parser_skill(raw, source_id=fname)
        result = skills.candidate_scorer_skill(profile, requirements)
        # Carry contact info forward (only an authorized role can later unmask).
        result["email"] = profile["email"]
        result["skills"] = profile["skills"]
        scored.append(result)

    ranked = sorted(scored, key=lambda c: c["match_percent"], reverse=True)
    for rank, c in enumerate(ranked, 1):
        c["rank"] = rank
    log.info("Resume Screener: ranked %d candidates (top score %.1f%%)",
             len(ranked), ranked[0]["match_percent"] if ranked else 0.0)
    return ranked


# --------------------------------------------------------------------------- #
# Stage 3 - Interview Scheduler Agent
# --------------------------------------------------------------------------- #
def run_scheduler(ctx: SecurityContext, ranked: List[Dict[str, Any]],
                  role_title: str) -> List[Dict[str, Any]]:
    ctx.authorize("send_invitations")

    shortlist = [c for c in ranked if c["match_percent"] >= SHORTLIST_THRESHOLD]
    slots = skills.suggest_interview_slots(len(shortlist))
    log.info("Scheduler: %d candidates shortlisted (threshold %.0f%%)",
             len(shortlist), SHORTLIST_THRESHOLD)

    emails: List[Dict[str, Any]] = []
    for cand, slot in zip(shortlist, slots):
        draft = skills.email_drafter_skill(cand, role_title=role_title, slot=slot)
        emails.append(draft)
        log.info("Scheduler: drafted invitation for %s at %s",
                 mask_pii(cand["name"]), slot)
    return emails


# --------------------------------------------------------------------------- #
# Stage 4 - Insights Agent
# --------------------------------------------------------------------------- #
def run_insights(ctx: SecurityContext, requirements: Dict[str, Any],
                 ranked: List[Dict[str, Any]], emails: List[Dict[str, Any]]) -> Dict[str, Any]:
    ctx.authorize("write_report")

    total = len(ranked)
    shortlisted = len(emails)
    avg_score = round(sum(c["match_percent"] for c in ranked) / total, 1) if total else 0.0

    skill_counter: Counter = Counter()
    for c in ranked:
        skill_counter.update(c.get("skills", []))
    top_skills = skill_counter.most_common(8)

    metrics = {
        "role": requirements.get("title"),
        "total_applicants": total,
        "shortlisted": shortlisted,
        "shortlist_rate_percent": round(100 * shortlisted / total, 1) if total else 0.0,
        "average_match_percent": avg_score,
        "top_skills_found": top_skills,
        "score_distribution": [c["match_percent"] for c in ranked],
        "candidate_labels": [c["name"].split()[0] for c in ranked],
    }

    report_md = _render_report(metrics, ranked)
    path = fs.save_report("recruitment_insights.md", report_md)
    metrics["report_path"] = path
    log.info("Insights: report saved to %s", path)
    return metrics


def _render_report(metrics: Dict[str, Any], ranked: List[Dict[str, Any]]) -> str:
    lines = [
        f"# Recruitment Insights Report - {metrics['role']}",
        "",
        "## Funnel Metrics",
        f"- **Total applicants:** {metrics['total_applicants']}",
        f"- **Shortlisted:** {metrics['shortlisted']}",
        f"- **Shortlist rate:** {metrics['shortlist_rate_percent']}%",
        f"- **Average match score:** {metrics['average_match_percent']}%",
        "",
        "## Top Skills Found in Applicant Pool",
    ]
    for skill, count in metrics["top_skills_found"]:
        lines.append(f"- {skill}: {count}")
    lines += ["", "## Ranked Candidates", "", "| Rank | Candidate | Match % | Recommendation |", "|---|---|---|---|"]
    for c in ranked:
        # Candidate names are kept; emails are intentionally omitted from the report.
        lines.append(f"| {c['rank']} | {c['name']} | {c['match_percent']} | {c['recommendation']} |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Full pipeline
# --------------------------------------------------------------------------- #
def run_pipeline(role: str = "recruiter", jd_filename: str = "") -> Dict[str, Any]:
    """Run the complete recruitment pipeline and return all artifacts.

    Args:
        role: RBAC role to run as (controls PII visibility & permissions).
        jd_filename: Optional specific JD file; defaults to first found.
    """
    ctx = SecurityContext(user="pipeline_runner", role=Role(role))
    log.info("=== Starting Smart HR Recruitment pipeline as role '%s' ===", role)

    requirements = run_jd_agent(ctx, jd_filename)
    ranked = run_resume_screener(ctx, requirements)
    emails = run_scheduler(ctx, ranked, role_title=requirements["title"])
    insights = run_insights(ctx, requirements, ranked, emails)

    log.info("=== Pipeline complete ===")
    return {
        "jd_requirements": requirements,
        "ranked_candidates": ranked,
        "interview_emails": emails,
        "insights": insights,
        "audit_log": ctx.audit_log,
    }


__all__ = ["run_pipeline", "run_jd_agent", "run_resume_screener",
           "run_scheduler", "run_insights", "SHORTLIST_THRESHOLD"]
