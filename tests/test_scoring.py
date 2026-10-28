from src import mcp_filesystem_server as fs
from src.sanitize import sanitize_untrusted_text
from src.skills import candidate_scorer_skill, jd_parser_skill, resume_parser_skill


def _requirements():
    return jd_parser_skill(fs.read_job_description())


def test_keyword_stuffer_has_no_experience_evidence():
    raw = fs.read_resume("resume_keyword_stuffer_priya_menon.txt")
    profile = resume_parser_skill(raw, "stuffer")
    assert profile["parse_status"] == "ok"
    assert len(profile["skills"]) >= 12
    assert profile["experienced_skills"] == []

    scored = candidate_scorer_skill(profile, _requirements())
    assert scored["keyword_stuffing_suspected"] is True
    assert scored["match_percent"] < 70


def test_experienced_candidate_outranks_keyword_stuffer():
    requirements = _requirements()
    strong = candidate_scorer_skill(
        resume_parser_skill(fs.read_resume("resume_senior_backend_daniel_okafor.txt"), "strong"),
        requirements,
    )
    stuffer = candidate_scorer_skill(
        resume_parser_skill(fs.read_resume("resume_keyword_stuffer_priya_menon.txt"), "stuffer"),
        requirements,
    )
    assert strong["match_percent"] > stuffer["match_percent"]
    assert strong["recommendation"] == "Strong match - shortlist"
    assert stuffer["keyword_stuffing_suspected"] is True


def test_scanned_pdf_is_flagged_not_ranked_as_a_person():
    text = fs.read_resume("resume_scanned_ravi_kumar.pdf")
    profile = resume_parser_skill(text, "scanned")
    assert profile["parse_status"] == "unreadable"
    assert profile["parse_warnings"]

    scored = candidate_scorer_skill(profile, _requirements())
    assert scored["shortlistable"] is False
    assert scored["match_percent"] == 0.0
    assert scored["recommendation"] == "Unreadable resume - manual review"


def test_injection_markers_are_removed_before_parsing():
    raw = fs.read_resume("resume_injection_ignore_previous_olivia_brown.txt")
    clean = sanitize_untrusted_text(raw, wrap=False)
    assert "<untrusted_data>" not in clean
    assert "ignore previous instructions" not in clean.lower()
    assert "[REDACTED]" in clean

    profile = resume_parser_skill(clean, "injection")
    assert profile["name"] == "Olivia Brown"


def test_resume_without_headings_uses_full_text_as_evidence():
    text = (
        "Alex Doe\n"
        "Backend Engineer\n"
        "Python, Docker, AWS\n"
        "Built REST APIs with Python and Docker on AWS.\n"
    )
    profile = resume_parser_skill(text, "no-headings")
    assert profile["parse_status"] == "ok"
    assert profile["experienced_skills"] == profile["skills"]
