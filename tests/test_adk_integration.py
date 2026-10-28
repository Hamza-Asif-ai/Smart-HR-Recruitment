import pytest

from src import adk_stages, agents
from src.orchestrator import (
    AgenticUnavailable,
    run_adk_pipeline,
    run_pipeline,
)
from src.security import AccessDeniedError


class FakeToolContext:
    def __init__(self, role="recruiter"):
        self.state = {"role": role}


def test_stage_tools_produce_the_same_deterministic_results():
    ctx = FakeToolContext()
    assert adk_stages.adk_jd_stage(ctx)["status"] == "ok"
    assert adk_stages.adk_screening_stage(ctx)["screened"] == 18
    assert adk_stages.adk_scheduler_stage(ctx)["invitations"] == 4
    assert adk_stages.adk_insights_stage(ctx)["total_applicants"] == 18

    ranked = ctx.state["ranked_candidates"]
    assert ctx.state["jd_requirements"]["title"]
    assert [c["rank"] for c in ranked] == list(range(1, 19))
    assert len(ctx.state["interview_emails"]) == 4
    assert ctx.state["insights"]["unreadable_resumes"] == 1
    assert any(entry["capability"] == "send_invitations" for entry in ctx.state["audit_log"])


def test_stage_tools_enforce_rbac():
    ctx = FakeToolContext(role="viewer")
    adk_stages.adk_jd_stage(ctx)
    with pytest.raises(AccessDeniedError):
        adk_stages.adk_screening_stage(ctx)


def test_agentic_pipeline_is_unavailable_without_a_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(AgenticUnavailable):
        run_adk_pipeline()


def test_pipeline_falls_back_to_deterministic_without_a_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = run_pipeline(use_agents=True)
    assert result["mode"] == "deterministic"
    assert len(result["ranked_candidates"]) == 18


@pytest.mark.skipif(not agents.ADK_AVAILABLE, reason="google-adk is not installed")
def test_adk_pipeline_builds_four_stage_agents():
    pipeline = agents.build_recruitment_pipeline()
    assert [a.name for a in pipeline.sub_agents] == [
        "jd_agent",
        "resume_screener_agent",
        "interview_scheduler_agent",
        "insights_agent",
    ]
    assert all(len(a.tools) == 1 for a in pipeline.sub_agents)
