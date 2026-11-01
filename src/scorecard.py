"""Per-candidate score cards and "why not shortlisted" explanations.

The card is an explainability layer over the deterministic scorer. Every piece
of evidence shown here is a quote that was already verified against the resume
by the Phase 3 extractor; the UI never asks a model to explain a decision.
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

from . import config

_MAX_QUOTES = 8


def _quote_items(candidate: Dict[str, Any]) -> List[Dict[str, str]]:
    evidence = candidate.get("evidence")
    if not isinstance(evidence, dict):
        profile = candidate.get("_profile")
        evidence = _evidence_from_profile(profile) if profile is not None else {}
    items: List[Dict[str, str]] = []
    for item in evidence.get("skills", []) or []:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote", "") or "").strip()
        if quote:
            items.append({"label": f"Skill: {item.get('skill', '')}", "quote": quote})
    experience = evidence.get("experience") or {}
    if isinstance(experience, dict):
        quote = str(experience.get("quote", "") or "").strip()
        if quote:
            years = experience.get("years", 0)
            items.append({"label": f"Experience ({years:g} yrs)".replace("(0 yrs)", "").strip(),
                          "quote": quote})
    for item in evidence.get("education", []) or []:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote", "") or "").strip()
        if quote:
            items.append({"label": f"Education: {item.get('degree', '')}", "quote": quote})
    return items[:_MAX_QUOTES]


def _evidence_from_profile(profile: Any) -> Dict[str, Any]:
    try:
        return {
            "skills": [
                {"skill": claim.skill, "quote": claim.evidence_quote}
                for claim in getattr(profile, "skills", []) or []
            ],
            "experience": {
                "years": getattr(getattr(profile, "experience", None), "years", 0) or 0,
                "quote": getattr(getattr(profile, "experience", None), "evidence_quote", "") or "",
            },
            "education": [
                {"degree": claim.degree, "quote": claim.evidence_quote}
                for claim in getattr(profile, "education", []) or []
            ],
        }
    except Exception:  # noqa: BLE001 - card rendering must never crash a run
        return {}


def why_not_shortlisted(
    candidate: Dict[str, Any],
    *,
    shortlist_threshold: Optional[float] = None,
) -> List[str]:
    """Human-readable blockers for a candidate outside the shortlist."""
    threshold = (config.SHORTLIST_THRESHOLD if shortlist_threshold is None
                 else float(shortlist_threshold))
    hit = min(100.0, float(candidate.get("match_percent", 0) or 0))
    reasons: List[str] = []
    if hit < threshold:
        reasons.append(
            f"Match score {hit:g}% is below the shortlist threshold of {threshold:g}%."
        )
    missing = [str(skill) for skill in (candidate.get("missing_required") or []) if skill]
    if missing:
        reasons.append("Missing required skills: " + ", ".join(sorted(missing)) + ".")
    need = candidate.get("_min_years", candidate.get("min_years", 0)) or 0
    years = candidate.get("experience_years", 0) or 0
    if need and years < need:
        reasons.append(
            f"Experience is {years:g} year(s), below the required {need:g} year(s)."
        )
    if candidate.get("parse_status") == "unreadable":
        reasons.append("The resume could not be read, so it needs manual review.")
    if candidate.get("keyword_stuffing_suspected"):
        reasons.append("Keyword stuffing was detected: listed skills are not backed by experience.")
    if candidate.get("hidden_text_suspected"):
        reasons.append("Hidden text was detected in the PDF.")
    if candidate.get("injection_suspected"):
        reasons.append("Prompt-injection indicators were detected in the resume.")
    if candidate.get("needs_human_review"):
        detail = str(candidate.get("confidence_reason", "") or "").strip()
        if detail and detail != "extraction verified":
            reasons.append("Flagged for human review: " + detail + ".")
    if not reasons:
        reasons.append(
            "No single blocking gap was found; stronger candidates ranked above this one."
        )
    return reasons


def build_scorecard(
    candidate: Dict[str, Any],
    *,
    shortlist_threshold: Optional[float] = None,
) -> Dict[str, Any]:
    threshold = (config.SHORTLIST_THRESHOLD if shortlist_threshold is None
                 else float(shortlist_threshold))
    hit = float(candidate.get("match_percent", 0) or 0)
    shortlisted = bool(
        candidate.get("recommendation") == "Strong match - shortlist"
        and hit >= threshold
    )
    return {
        "name": str(candidate.get("name", "") or "Unknown Candidate"),
        "email": str(candidate.get("email", "") or ""),
        "match_percent": hit,
        "recommendation": str(candidate.get("recommendation", "") or ""),
        "shortlisted": shortlisted,
        "confidence": float(candidate.get("confidence", candidate.get("_confidence", 0)) or 0),
        "evidence": _quote_items(candidate),
        "matched_required": [str(s) for s in (candidate.get("matched_required") or [])],
        "missing_required": [str(s) for s in (candidate.get("missing_required") or [])],
        "why_not_shortlisted": [] if shortlisted else why_not_shortlisted(
            candidate, shortlist_threshold=threshold
        ),
    }


def _chip(text: str, css_class: str) -> str:
    return f'<span class="skill-chip {css_class}">{html.escape(text)}</span>'


def render_score_cards(
    candidates: List[Dict[str, Any]],
    *,
    shortlist_threshold: Optional[float] = None,
) -> str:
    """Render responsive per-candidate cards with evidence and gaps."""
    if not candidates:
        return '<div class="empty-state">No candidates yet — scan the inbox to build score cards.</div>'
    cards = []
    for candidate in candidates:
        card = build_scorecard(candidate, shortlist_threshold=shortlist_threshold)
        status_class = "shortlist" if card["shortlisted"] else "review"
        status_label = "Shortlisted" if card["shortlisted"] else card["recommendation"]
        matched = "".join(_chip(skill, "") for skill in card["matched_required"][:8])
        missing = "".join(_chip(skill, "missing") for skill in card["missing_required"][:8])
        skills_html = (
            '<div class="skills-row">' + (matched + missing or
                                          '<span class="cand-email">No required-skill data.</span>')
            + "</div>"
        )
        if card["evidence"]:
            quotes = "".join(
                '<div class="score-quote"><span class="score-quote-label">'
                f'{html.escape(item["label"])}</span>'
                f'<div class="score-quote-text">“{html.escape(item["quote"])}”</div></div>'
                for item in card["evidence"]
            )
        else:
            quotes = ('<div class="cand-email">No verified evidence quotes were available '
                      'for this candidate.</div>')
        why_html = ""
        if card["why_not_shortlisted"]:
            reasons = "".join(
                f"<li>{html.escape(reason)}</li>" for reason in card["why_not_shortlisted"]
            )
            why_html = (
                '<div class="why-not"><div class="why-not-title">Why not shortlisted?</div>'
                f"<ul>{reasons}</ul></div>"
            )
        cards.append(f"""
        <div class="score-card">
          <div class="score-card-head">
            <div>
              <div class="score-card-name">{html.escape(card['name'])}</div>
              <div class="cand-email">{html.escape(card['email'])}</div>
            </div>
            <div class="score-card-score">
              <div class="match-pct">{card['match_percent']:g}%</div>
              <span class="chip {status_class}">{html.escape(status_label)}</span>
            </div>
          </div>
          <div class="score-card-meta">
            Confidence {card['confidence'] * 100:.0f}% · {len(card['evidence'])} verified quote(s)
          </div>
          <div class="score-card-section-title">Required skills</div>
          {skills_html}
          <div class="score-card-section-title">Evidence from the resume</div>
          <div class="score-quotes">{quotes}</div>
          {why_html}
        </div>""")
    return f'<div class="score-card-grid">{"".join(cards)}</div>'


__all__ = ["build_scorecard", "render_score_cards", "why_not_shortlisted"]
