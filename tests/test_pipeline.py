from pathlib import Path

import pytest

from src import config
from src.orchestrator import run_pipeline
from src.security import AccessDeniedError


def test_pipeline_runs_offline_and_produces_artifacts():
    result = run_pipeline(role="recruiter")

    ranked = result["ranked_candidates"]
    assert len(ranked) == 18
    assert [c["rank"] for c in ranked] == list(range(1, 19))
    assert ranked == sorted(ranked, key=lambda c: c["match_percent"], reverse=True)

    shortlisted = [
        c for c in ranked
        if c.get("shortlistable", True) and c["match_percent"] >= config.SHORTLIST_THRESHOLD
    ]
    assert len(result["interview_emails"]) == len(shortlisted)
    assert len(shortlisted) > 0

    by_name = {c["name"]: c for c in ranked}
    stuffer = by_name["Priya Menon"]
    assert stuffer["keyword_stuffing_suspected"] is True
    assert stuffer["match_percent"] < config.SHORTLIST_THRESHOLD
    assert all(e["candidate"] != "Priya Menon" for e in result["interview_emails"])

    unreadable = by_name["Unknown Candidate"]
    assert unreadable["parse_status"] == "unreadable"
    assert unreadable["shortlistable"] is False
    assert all(e["candidate"] != "Unknown Candidate" for e in result["interview_emails"])
    assert result["insights"]["unreadable_resumes"] == 1
    assert result["insights"]["keyword_stuffing_suspected"] >= 1

    report_path = Path(result["insights"]["report_path"])
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert "Recruitment Insights Report" in report
    assert "Unreadable resumes (manual review):** 1" in report

    assert any(entry["capability"] == "write_report" for entry in result["audit_log"])


def test_viewer_role_is_blocked_before_scoring():
    with pytest.raises(AccessDeniedError):
        run_pipeline(role="viewer")
