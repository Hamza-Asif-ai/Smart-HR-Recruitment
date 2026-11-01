"""Phase 4 evaluation and fairness suite.

Fixtures live in ``tests/eval`` so they are reviewable independent of the test
code. All resumes/replies are synthetic and use example.com contacts only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import fairness, guardrails, phase3_runner, runtime, skills, tools
from src import tracing

EVAL_DIR = Path(__file__).parent / "eval"


def _load(name: str) -> dict:
    return json.loads((EVAL_DIR / name).read_text(encoding="utf-8"))


def _score(resume_text: str, requirements: dict, *, source_id: str = "case",
           blind: bool = False) -> dict:
    rc = runtime.new_run_context(max_steps=10)
    return phase3_runner.analyze_resume_text(
        resume_text, requirements, source_id=source_id,
        run_context=rc, llm_enabled=False, blind=blind,
    )


class _ToolContext:
    def __init__(self, requirements: dict, resume_text: str, trace):
        self.state = {
            "role": "recruiter",
            "user": "phase4_eval",
            "jd_requirements": requirements,
            "resume_texts": {"resume.txt": resume_text},
            "trace": trace,
        }


def _run_tool_chain(requirements: dict, resume_text: str) -> tuple[list[str], float]:
    trace = tracing.TraceRecorder()
    ctx = _ToolContext(requirements, resume_text, trace)
    tools.analyze_job_description(ctx)
    tools.extract_resume_profile(ctx, "resume.txt")
    tools.screen_resume(ctx, "resume.txt")
    events = list(reversed(trace.get_traces(50)))
    sequence = [e["tool"] for e in events if e["action"] == "tool_call"]
    score = ctx.state["ranked_candidates"][0]["match_percent"]
    return sequence, score


def test_golden_set_scores_stay_inside_expected_bands():
    fixture = _load("golden_set.json")
    requirements = skills.jd_parser_skill(fixture["jd"])
    assert len(fixture["cases"]) >= 8

    failures = []
    for case in fixture["cases"]:
        result = _score(case["resume"], requirements, source_id=case["id"])
        low, high = case["expected_band"]
        if not low <= result["match_percent"] <= high:
            failures.append(
                f"{case['id']}: {result['match_percent']} outside [{low}, {high}]")
        for skill in case["expected_skills"]:
            if skill not in result["skills"]:
                failures.append(f"{case['id']}: missing expected skill {skill}")
    assert not failures, "\n".join(failures)


def test_golden_set_keyword_stuffer_is_flagged_for_review():
    fixture = _load("golden_set.json")
    requirements = skills.jd_parser_skill(fixture["jd"])
    case = next(c for c in fixture["cases"] if c["id"] == "keyword_stuffer")
    result = _score(case["resume"], requirements, source_id=case["id"])
    assert result["keyword_stuffing_suspected"] is True
    assert result["needs_human_review"] is True
    assert result["match_percent"] < 75


def test_injection_set_does_not_change_score_or_tool_calls():
    fixture = _load("injection_set.json")
    requirements = skills.jd_parser_skill(fixture["jd"])
    expected_tools = fixture["expected_tools"]

    for case in fixture["cases"]:
        clean_text = case["base_resume"]
        injected_text = clean_text + case["injection"]

        clean_score = _score(clean_text, requirements, source_id=case["id"])
        injected_score = _score(injected_text, requirements, source_id=case["id"])
        assert injected_score["match_percent"] == clean_score["match_percent"], case["id"]
        assert injected_score["injection_suspected"] is True, case["id"]

        clean_tools, clean_tool_score = _run_tool_chain(requirements, clean_text)
        injected_tools, injected_tool_score = _run_tool_chain(requirements, injected_text)
        assert clean_tools == expected_tools, case["id"]
        assert injected_tools == clean_tools, case["id"]
        assert injected_tool_score == clean_tool_score, case["id"]


def test_reply_triage_labels_include_required_languages_and_phrases():
    fixture = _load("reply_labels.json")
    messages = fixture["messages"]
    texts = {m["text"] for m in messages}
    normalized = {text.lower() for text in texts}

    assert len(messages) >= 30
    assert "I am not available" in texts
    assert "I accepted another offer and cannot join" in texts
    assert "can we change the time?" in normalized
    assert any(m["language"] == "roman_ur" for m in messages)
    assert any(m["language"] == "ur" for m in messages)

    failures = []
    for message in messages:
        result = guardrails.classify_reply(message["text"])
        if result.intent != message["intent"]:
            failures.append(
                f"{message['id']}: expected {message['intent']}, got {result.intent}")
        if result.requires_human != message["requires_human"]:
            failures.append(
                f"{message['id']}: human flag expected {message['requires_human']}, "
                f"got {result.requires_human} (confidence {result.confidence})")
    assert not failures, "\n".join(failures)


def test_roman_urdu_and_urdu_decline_phrases_are_not_confirmations():
    for text in (
        "Main nahi aa sakta.",
        "Mujhe dusri job accept kar li hai.",
        "میں نہیں آ سکتا۔",
        "میں نے دوسری نوکری قبول کر لی ہے۔",
    ):
        result = guardrails.classify_reply(text)
        assert result.intent == "decline", text


def test_blind_mode_masks_identity_and_leaves_scores_unchanged():
    fixture = _load("golden_set.json")
    requirements = skills.jd_parser_skill(fixture["jd"])

    for case in fixture["cases"]:
        plain = _score(case["resume"], requirements, source_id=case["id"])
        blind = _score(case["resume"], requirements, source_id=case["id"], blind=True)
        assert blind["match_percent"] == plain["match_percent"], case["id"]
        assert blind["blind_mode"] is True
        assert blind["name"] == "Candidate"
        assert blind["email"] == ""
        assert "example.com" not in blind.get("email", "")

    masked = fairness.blind_resume_text("Amara Okafor\namara@example.com\nPython and SQL")
    assert masked.splitlines()[0] == fairness.MASK_TOKEN
    assert "Amara Okafor" not in masked
    assert "amara@example.com" not in masked


def test_blind_name_swaps_pass_the_four_fifths_rule():
    fixture = _load("golden_set.json")
    requirements = skills.jd_parser_skill(fixture["jd"])
    base = next(c for c in fixture["cases"] if c["id"] == "strong_backend")["resume"]
    names = ["Amara Okafor", "José Álvarez", "Chen Li", "Fatima Noor", "Alex Morgan"]
    groups: dict[str, list[float]] = {}
    for index, name in enumerate(names):
        swapped = base.replace("Amara Okafor", name)
        blind = _score(swapped, requirements, source_id=f"swap-{index}", blind=True)
        groups[name] = [blind["match_percent"]]

    report = fairness.group_scores_report(groups, threshold=75.0)
    assert report["passes_4_5"] is True
    assert report["impacted_groups"] == []
    assert len({item["rate"] for item in report["groups"].values()}) == 1


def test_adverse_impact_report_matches_synthetic_fixture():
    fixture = _load("adverse_impact.json")
    report = fairness.adverse_impact_report(
        fixture["groups"], four_fifths=fixture["four_fifths"])

    expected = fixture["expected"]
    assert report["reference_group"] == expected["reference_group"]
    assert report["reference_rate"] == expected["reference_rate"]
    assert report["impacted_groups"] == expected["impacted_groups"]
    assert report["passes_4_5"] == expected["passes_4_5"]
    assert report["groups"]["synthetic_beta"]["rate"] == 0.5


def test_adverse_impact_report_passes_when_rates_are_equal():
    report = fairness.adverse_impact_report(
        {"a": [True] * 6 + [False] * 4,
         "b": [True] * 3 + [False] * 2,
         "c": {"selected": 6, "total": 10}})
    assert report["passes_4_5"] is True
    assert report["impacted_groups"] == []


def test_adk_evalset_has_required_metrics_and_safe_trajectories():
    eval_set_mod = pytest.importorskip("google.adk.evaluation.eval_set")
    config_mod = pytest.importorskip("google.adk.evaluation.eval_config")

    eval_set = eval_set_mod.EvalSet.model_validate(_load("adk_evalset.json"))
    config = config_mod.EvalConfig.model_validate(_load("adk_eval_config.json"))

    assert "tool_trajectory_avg_score" in config.criteria
    assert "rubric_based_tool_use_quality_v1" in config.criteria
    assert len(eval_set.eval_cases) >= 2

    trajectories = []
    for case in eval_set.eval_cases:
        assert case.rubrics
        invocation = case.conversation[0]
        calls = invocation.intermediate_data.tool_uses
        names = [call.name for call in calls]
        trajectories.append(names)
        assert all("send" not in name.lower() for name in names)
        assert "analyze_job_description" in names
        assert "screen_resume" in names
    assert trajectories[0] == trajectories[1]
