"""Phase 3 integration: shared runner, coordinator wiring, approval, tracing."""

from __future__ import annotations

import json

import pytest

from src import config, extractor, memory, phase3_runner, runtime, skills, tools
from src.approval import ApprovalQueue, ApprovalRequiredError, send_approved_draft
from src.models import CandidateProfile, EducationClaim, ExperienceClaim, SkillClaim
from src.security import Role

RESUME = """Nadia Petrova
nadia@example.com
5 years of experience building Python services with SQL and AWS.
B.Sc. in Computer Science from State University.
"""


def _profile() -> CandidateProfile:
    return CandidateProfile(
        name="Nadia Petrova",
        email="nadia@example.com",
        skills=[
            SkillClaim(skill="python", evidence_quote="Python services"),
            SkillClaim(skill="sql", evidence_quote="SQL"),
        ],
        experience=ExperienceClaim(years=5, evidence_quote="5 years of experience"),
        education=[EducationClaim(degree="B.Sc.",
                                  evidence_quote="B.Sc. in Computer Science")],
    )


def _requirements():
    return skills.jd_parser_skill("Python SQL AWS engineer, 3 years")


def test_runner_uses_deterministic_fallback_and_records_budget_and_trace(tmp_path):
    store = memory.CandidateMemory(db_path=str(tmp_path / "m.sqlite3"),
                                   hmac_key=b"test-key")
    store.remember_candidate("nadia@example.com", "shortlist", "previous HR decision")
    rc = runtime.new_run_context(max_steps=10)

    result = phase3_runner.analyze_resume_text(
        RESUME, _requirements(), source_id="nadia.txt",
        run_context=rc, memory=store, llm_enabled=False)

    assert result["extraction_mode"] == "deterministic"
    assert result["source_id"] == "nadia.txt"
    assert result["confidence"] > 0
    assert "shortlisted" in result["memory_hint"]
    assert rc.budget.steps_used == 1
    traces = rc.trace.get_traces(20)
    assert traces
    assert all(t["run_id"] == rc.trace.run_id for t in traces)
    assert any(t["action"] == "analyze_resume" for t in traces)


def test_runner_accepts_validated_llm_profile_and_drops_fabricated_claim(monkeypatch):
    rc = runtime.new_run_context(max_steps=10)
    profile = _profile()
    profile.skills.append(SkillClaim(skill="kubernetes", evidence_quote="Kubernetes expert"))

    def fake_extract(text, *, budget=None, source_id="", **kwargs):
        budget.before_model_call()
        budget.record_tokens(42)
        return extractor.verify_candidate_profile(profile, text)

    monkeypatch.setattr(extractor, "extract_candidate_profile", fake_extract)
    result = phase3_runner.analyze_resume_text(
        RESUME, _requirements(), source_id="nadia.txt",
        run_context=rc, llm_enabled=True, model="configured-model")

    assert result["extraction_mode"] == "validated_llm"
    assert result["dropped_claims"] == 1
    assert rc.budget.model_calls == 1
    assert rc.budget.tokens == 42


def test_model_status_startup_check(monkeypatch):
    monkeypatch.setattr(phase3_runner, "_MODEL_STATUS", None)
    monkeypatch.setattr(config, "get_gemini_api_key", lambda: "")
    assert phase3_runner.model_status(force=True).mode == "deterministic"

    monkeypatch.setattr(config, "get_gemini_api_key", lambda: "key")
    monkeypatch.setattr(config, "get_gemini_model", lambda: "configured-model")
    monkeypatch.setattr(extractor, "check_model_available", lambda model: True)
    status = phase3_runner.model_status(force=True)
    assert status.mode == "agentic"
    assert status.model == "configured-model"

    def unavailable(model):
        raise extractor.ModelUnavailableError("missing")

    monkeypatch.setattr(extractor, "check_model_available", unavailable)
    assert phase3_runner.model_status(force=True).degraded is True


def test_screen_resume_tool_consumes_validated_profile_and_memory(tmp_path):
    store = memory.CandidateMemory(db_path=str(tmp_path / "m.sqlite3"),
                                   hmac_key=b"test-key")
    store.remember_candidate("nadia@example.com", "shortlist", "prior")

    class Ctx:
        def __init__(self):
            self.state = {
                "role": Role.RECRUITER.value,
                "jd_requirements": _requirements(),
                "resume_texts": {"n.txt": RESUME},
                "validated_profiles": {"n.txt": _profile()},
                "memory": store,
            }

    result = tools.screen_resume(Ctx(), "n.txt")
    assert result["status"] == "ok"
    assert result["needs_human_review"] is False
    assert result["confidence"] > 0
    assert result["source_id"] == "n.txt"


def test_coordinator_has_no_send_tool_and_no_duplicate_screeners():
    from src import coordinator

    if not coordinator.ADK_AVAILABLE:
        pytest.skip("google-adk not installed")
    root = coordinator.build_coordinator(model="configured-model",
                                         source_ids=["one.txt", "two.txt"])
    names = [a.name for a in root.sub_agents]
    assert "parallel_screeners" in names
    assert "screener" not in names
    assert "critic_loop" in names

    def walk(agent):
        yield agent
        for child in getattr(agent, "sub_agents", []) or []:
            yield from walk(child)

    tool_names = []
    for agent in walk(root):
        tool_names.extend(getattr(t, "name", "") for t in (getattr(agent, "tools", []) or []))
    assert all("send" not in name.lower() for name in tool_names)


def test_unapproved_drafts_are_refused_by_the_deterministic_gate():
    queue = ApprovalQueue(max_size=5)
    draft = queue.stage({"to": "a@example.com", "subject": "Hi", "body": "Body"})
    with pytest.raises(ApprovalRequiredError):
        send_approved_draft(draft, sender_email="hr@example.com", sender_name="HR",
                            api_key="key", send_func=lambda *a: None)


def test_trace_export_is_per_run_and_contains_budget(tmp_path):
    rc = runtime.new_run_context(max_steps=5)
    rc.trace.record("agent", "action", output_summary="nadia@example.com")
    path = tmp_path / "trace.json"
    rc.trace.export_run(str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload
    assert payload[0]["run_id"] == rc.trace.run_id
    assert "nadia@example.com" not in path.read_text(encoding="utf-8")


def test_summarize_budget_includes_run_id_and_degradation():
    rc = runtime.new_run_context(max_steps=5)
    rc.budget.degraded = True
    rc.budget.degraded_reason = "run exceeded MAX_LLM_CALLS=1"
    line = phase3_runner.summarize_budget(rc)
    assert rc.trace.run_id[:8] in line
    assert "MAX_LLM_CALLS" in line


def test_coordinator_requires_a_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(phase3_runner.CoordinatorUnavailable):
        phase3_runner.run_coordinator(
            requirements=_requirements(), resume_texts={"n.txt": RESUME},
            source_ids=["n.txt"], model="configured-model")
