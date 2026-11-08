"""Regression tests for the Phase 1 correctness fixes (items a-h)."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from src import config
from src import agents, skills
from src.orchestrator import run_pipeline
from src.security import Role, SecurityContext, mask_pii

ROOT = Path(__file__).resolve().parents[1]


# a) Experience years -----------------------------------------------------------------
def test_education_dates_are_not_counted_as_work_experience():
    fresh_grad = (
        "Aisha Rahman\n"
        "Education\n"
        "B.Sc., Computer Science - University of Toronto (2017 - 2021)\n"
    )
    assert skills.estimate_experience_years(fresh_grad) == 0


def test_year_ranges_use_full_years_and_merge_overlaps():
    text = (
        "Experience\n"
        "Engineer, Acme (2018 - 2021)\n"
        "Senior Engineer, Beta (2020 - 2023)\n"
    )
    assert skills.estimate_experience_years(text) == 5.0

    present = "Engineer, Acme (2017 - Present)"
    expected = datetime.now().year - 2017
    assert skills.estimate_experience_years(present) == expected


# b) Skill matching -------------------------------------------------------------------
def test_skill_matching_uses_word_boundaries():
    assert skills._detect_skills("Java developer") == ["java"]
    assert "java" not in skills._detect_skills("JavaScript developer")
    assert "rest" not in skills._detect_skills("restaurant manager")
    assert "aws" not in skills._detect_skills("class3 network")


def test_ambiguous_skills_need_list_or_programming_context():
    listed = skills._detect_skills(
        "Skills\nGo, R, C, Microsoft Excel, Microsoft Word"
    )
    assert {"go", "r", "c", "excel", "word"} <= set(listed)
    assert "go" not in skills._detect_skills("I want to go home")
    assert "excel" not in skills._detect_skills("We excel at scale")
    assert "word" not in skills._detect_skills("Please word the summary")
    assert "r" not in skills._detect_skills("R is a letter in the alphabet")


# c) JD parsing and any-of groups ------------------------------------------------------
def test_jd_required_section_is_not_polluted_by_responsibilities_or_nice_to_have():
    jd = """Job Title: Backend Engineer
Responsibilities:
- Build services with Terraform and gRPC.
Required Skills:
- Python
- Experience with at least one cloud platform (GCP preferred, AWS/Azure acceptable).
Nice to Have:
- Terraform
- Go
Qualifications:
- Bachelor's degree.
- 5+ years of experience.
"""
    parsed = skills.jd_parser_skill(jd)
    assert "terraform" not in parsed["required_skills"]
    assert "grpc" not in parsed["required_skills"]
    assert "terraform" in parsed["nice_to_have"]
    assert "go" in parsed["nice_to_have"]
    assert ["aws", "azure", "gcp"] in parsed["required_skill_groups"]
    assert parsed["min_years"] == 5


def test_any_of_cloud_group_is_satisfied_by_one_provider():
    requirements = {
        "required_skills": ["python"],
        "required_skill_groups": [["aws", "azure", "gcp"]],
        "nice_to_have": [],
        "min_years": 0,
    }
    candidate = {
        "name": "Cloud Candidate",
        "skills": ["python", "aws"],
        "experienced_skills": ["python", "aws"],
        "experience_years": 3,
        "education": ["BSC"],
        "parse_status": "ok",
    }
    scored = skills.candidate_scorer_skill(candidate, requirements)
    assert scored["matched_required_groups"] == [["aws"]]
    assert scored["missing_required_groups"] == []
    assert scored["listed_skill_coverage_percent"] == 100.0


# d) One env-driven config -------------------------------------------------------------
def test_thresholds_are_shared_by_scorer_orchestrator_and_agents():
    assert config.SHORTLIST_THRESHOLD == 60.0
    assert config.REVIEW_THRESHOLD == 55.0
    assert skills.recommendation_for_score(59.9) == "Possible match - review"
    assert skills.recommendation_for_score(60.0) == "Strong match - shortlist"
    assert f"{config.SHORTLIST_THRESHOLD:.0f}%" in agents.SCHEDULER_AGENT_INSTRUCTION


def test_thresholds_can_be_overridden_from_the_environment():
    env = os.environ.copy()
    env["SHORTLIST_THRESHOLD"] = "80"
    env["REVIEW_THRESHOLD"] = "60"
    out = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "from src import config; print(config.SHORTLIST_THRESHOLD, config.REVIEW_THRESHOLD)",
        ],
        cwd=ROOT,
        env=env,
        text=True,
    )
    assert out.strip() == "80.0 60.0"


# e) RBAC default-deny and hiring-manager degradation ---------------------------------
def test_unknown_capabilities_are_denied_by_default():
    assert SecurityContext(role=Role.ADMIN).can("not_a_real_capability") is False
    assert SecurityContext(role=Role.HIRING_MANAGER).can("send_invitations") is False
    assert SecurityContext(role=Role.HIRING_MANAGER).can("view_unmasked_pii") is False


def test_hiring_manager_run_skips_scheduling_and_reporting_and_masks_pii():
    result = run_pipeline(role="hiring_manager")
    assert result["interview_emails"] == []
    assert result["insights"]["report_skipped"] is True
    assert result["insights"]["report_path"] is None
    assert result["ranked_candidates"]
    assert all(c["email"] in {"[EMAIL]", ""} for c in result["ranked_candidates"])
    assert all(c["name"] == "[NAME]" for c in result["ranked_candidates"])

    denied = {
        entry["capability"] for entry in result["audit_log"]
        if entry["granted"] is False
    }
    assert {"send_invitations", "write_report"} <= denied


# f) PII masking ---------------------------------------------------------------------
def test_phone_masking_does_not_eat_years_or_plain_numbers():
    for plain in ("2019 - 2022", "2020-2024", "Class of 2021", "4155550103"):
        assert mask_pii(plain) == plain
    assert mask_pii("+1 (415) 555-0103") == "[PHONE]"
    assert mask_pii("+91 98765 55510") == "[PHONE]"
    assert mask_pii("+234 1 555 0110") == "[PHONE]"
    assert mask_pii("+353 1 555 0111") == "[PHONE]"


def test_profile_urls_and_tracebacks_are_masked():
    assert mask_pii("linkedin.com/in/nadia-petrova") == "[URL]"
    traceback = (
        r'Traceback (most recent call last): File "C:\Users\ana\app.py", line 5'
        "\nPermissionError: denied"
    )
    masked = mask_pii(traceback)
    assert "ana" not in masked
    assert r"C:\Users" not in masked
    assert "[TRACEBACK]" in masked


# g) Timezone-aware interview slots ---------------------------------------------------
def test_interview_slots_use_the_configured_timezone():
    utc = skills.suggest_interview_slots(1, timezone="UTC")
    assert len(utc) == 1
    assert utc[0].endswith("UTC")
    other = skills.suggest_interview_slots(1, timezone="America/New_York")
    assert len(other) == 1
    assert not other[0].endswith("IST")


# h) Unused dependencies and superseded agent stubs --------------------------------
def test_phase3_modules_and_required_dependencies_exist():
    for module in ("memory.py", "budget.py", "extractor.py", "scoring.py",
                   "guardrails.py", "approval.py", "coordinator.py"):
        assert (ROOT / "src" / module).exists(), module
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "pydantic" in requirements
    for dependency in ("tenacity", "chromadb"):
        assert dependency not in requirements
    for name in (
        "build_planner_agent",
        "build_reflection_agent",
        "build_reply_classifier_agent",
        "build_supervisor_agent",
    ):
        assert not hasattr(agents, name)
