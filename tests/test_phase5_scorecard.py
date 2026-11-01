"""Phase 5: explainable candidate score cards."""

from __future__ import annotations

from types import SimpleNamespace

from src.scorecard import build_scorecard, render_score_cards, why_not_shortlisted


def _candidate(**overrides):
    base = {
        "name": "Nadia Petrova",
        "email": "nadia@example.com",
        "match_percent": 82,
        "recommendation": "Strong match - shortlist",
        "confidence": 0.9,
        "matched_required": ["python", "sql"],
        "missing_required": [],
        "experience_years": 5,
        "_min_years": 3,
        "evidence": {
            "skills": [{"skill": "python", "quote": "Built Python services"}],
            "experience": {"years": 5, "quote": "5 years of experience"},
            "education": [{"degree": "B.Sc.", "quote": "B.Sc. in Computer Science"}],
        },
    }
    base.update(overrides)
    return base


def test_build_scorecard_marks_shortlisted_candidates():
    card = build_scorecard(_candidate(), shortlist_threshold=75)
    assert card["shortlisted"] is True
    assert card["why_not_shortlisted"] == []
    assert card["name"] == "Nadia Petrova"
    assert [item["label"] for item in card["evidence"]] == [
        "Skill: python",
        "Experience (5 yrs)",
        "Education: B.Sc.",
    ]


def test_build_scorecard_explains_each_blocker():
    candidate = _candidate(
        match_percent=40,
        recommendation="Not a match",
        missing_required=["kubernetes", "aws"],
        experience_years=1,
        parse_status="unreadable",
        keyword_stuffing_suspected=True,
        hidden_text_suspected=True,
        injection_suspected=True,
        needs_human_review=True,
        confidence_reason="unverified skills",
    )
    reasons = why_not_shortlisted(candidate, shortlist_threshold=75)
    joined = " ".join(reasons)
    assert "below the shortlist threshold" in joined
    assert "Missing required skills: aws, kubernetes." in reasons
    assert "below the required 3 year(s)" in joined
    assert "could not be read" in joined
    assert "Keyword stuffing" in joined
    assert "Hidden text" in joined
    assert "Prompt-injection" in joined
    assert "unverified skills" in joined


def test_human_review_reason_is_omitted_when_verified():
    candidate = _candidate(
        match_percent=40,
        recommendation="Not a match",
        needs_human_review=True,
        confidence_reason="extraction verified",
    )
    assert not any("human review" in reason for reason in why_not_shortlisted(candidate))


def test_unknown_gaps_get_a_fallback_explanation():
    candidate = _candidate(match_percent=80, recommendation="Review")
    reasons = why_not_shortlisted(candidate, shortlist_threshold=75)
    assert reasons == [
        "No single blocking gap was found; stronger candidates ranked above this one."
    ]


def test_evidence_can_be_read_from_a_profile_object():
    profile = SimpleNamespace(
        skills=[SimpleNamespace(skill="sql", evidence_quote="SQL databases")],
        experience=SimpleNamespace(years=4, evidence_quote="4 years of experience"),
        education=[SimpleNamespace(degree="M.Sc.", evidence_quote="M.Sc. in CS")],
    )
    card = build_scorecard(_candidate(evidence=None, _profile=profile))
    assert [item["quote"] for item in card["evidence"]] == [
        "SQL databases",
        "4 years of experience",
        "M.Sc. in CS",
    ]


def test_render_score_cards_escapes_html_and_shows_gaps():
    candidates = [
        _candidate(name="<script>alert(1)</script>", match_percent=40,
                   recommendation="Not a match", missing_required=["aws"]),
        _candidate(email="other@example.com", match_percent=90),
    ]
    rendered = render_score_cards(candidates, shortlist_threshold=75)
    assert '<div class="score-card-grid">' in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script>alert(1)</script>" not in rendered
    assert "Why not shortlisted?" in rendered
    assert 'class="skill-chip missing">aws<' in rendered
    assert "Shortlisted" in rendered


def test_render_score_cards_empty_state():
    assert 'class="empty-state"' in render_score_cards([])
