from pathlib import Path

import pytest

from src.orchestrator import run_pipeline
from src.security import AccessDeniedError


def test_pipeline_runs_offline_and_produces_artifacts():
    result = run_pipeline(role="recruiter")

    ranked = result["ranked_candidates"]
    assert len(ranked) == 18
    assert [c["rank"] for c in ranked] == list(range(1, 19))
    assert ranked == sorted(ranked, key=lambda c: c["match_percent"], reverse=True)

    shortlisted = [c for c in ranked if c["match_percent"] >= 70]
    assert len(result["interview_emails"]) == len(shortlisted)
    assert len(shortlisted) > 0

    report_path = Path(result["insights"]["report_path"])
    assert report_path.exists()
    assert "Recruitment Insights Report" in report_path.read_text(encoding="utf-8")

    assert any(entry["capability"] == "write_report" for entry in result["audit_log"])


def test_viewer_role_is_blocked_before_scoring():
    with pytest.raises(AccessDeniedError):
        run_pipeline(role="viewer")
