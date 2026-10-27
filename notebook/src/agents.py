"""
agents.py
=========
The four specialized agents of the Smart HR Recruitment system, built with
Google ADK (Agent Development Kit).

    1. JD Agent             - analyzes a job description -> structured requirements
    2. Resume Screener      - parses & scores resumes -> ranked candidates
    3. Interview Scheduler  - drafts invitation emails + proposes slots
    4. Insights Agent       - aggregates funnel metrics -> report

Each agent is an ``LlmAgent`` (Gemini-backed) equipped with the reusable
Agent Skills from ``skills.py`` exposed as ``FunctionTool``s. The agents share
state through the ADK session via ``output_key``, which is how ADK performs
multi-agent orchestration (one agent's output becomes the next agent's input).

If ``google-adk`` is not installed, ``ADK_AVAILABLE`` is False and the
deterministic orchestrator in ``orchestrator.py`` runs the same skills directly.
"""

from __future__ import annotations

# Default Gemini model used by every agent. Flash is fast + cheap for Kaggle.
DEFAULT_MODEL = "gemini-2.0-flash"

try:
    from google.adk.agents import LlmAgent, SequentialAgent
    from google.adk.tools import FunctionTool
    ADK_AVAILABLE = True
except Exception:  # pragma: no cover - ADK not installed in this env
    ADK_AVAILABLE = False
    LlmAgent = SequentialAgent = FunctionTool = object  # type: ignore

from . import skills


# --------------------------------------------------------------------------- #
# Agent instructions (system prompts). Kept declarative and explicit so the
# behavior is reproducible.
# --------------------------------------------------------------------------- #
JD_AGENT_INSTRUCTION = """\
You are the JD Agent, an expert technical recruiter.
Given a raw job description, call the `jd_parser_skill` tool to extract a
structured set of requirements. Return ONLY the resulting JSON with keys:
title, required_skills, nice_to_have, min_years, seniority, qualifications.
"""

RESUME_AGENT_INSTRUCTION = """\
You are the Resume Screener Agent.
Read each available resume with the file tools, call `resume_parser_skill` to
structure it, then call `candidate_scorer_skill` against the role requirements
produced by the JD Agent (available in session state under 'jd_requirements').
Return a JSON list of candidates ranked by match_percent (descending).
"""

SCHEDULER_AGENT_INSTRUCTION = """\
You are the Interview Scheduler Agent.
For each shortlisted candidate (match_percent >= 70), call `email_drafter_skill`
to write a personalized interview invitation and assign a proposed slot.
Return the list of email drafts. Never expose other candidates' PII in an email.
"""

INSIGHTS_AGENT_INSTRUCTION = """\
You are the Insights Agent.
Aggregate the recruitment funnel into hiring metrics: total applicants,
shortlist rate, average match score, and the most common skills found. Use the
`save_report` file tool to persist a markdown report. Return the metrics JSON.
"""


# --------------------------------------------------------------------------- #
# Tool wrapping: expose skills + MCP file ops as ADK FunctionTools.
# --------------------------------------------------------------------------- #
def _build_tools():
    """Wrap skill callables (and file ops) as ADK FunctionTools."""
    from . import mcp_filesystem_server as fs

    callables = {
        "jd": [skills.jd_parser_skill, fs.read_job_description],
        "resume": [
            skills.resume_parser_skill,
            skills.candidate_scorer_skill,
            fs.list_resumes,
            fs.read_resume,
        ],
        "scheduler": [skills.email_drafter_skill, skills.suggest_interview_slots],
        "insights": [fs.save_report],
    }
    return {k: [FunctionTool(fn) for fn in v] for k, v in callables.items()}


def build_jd_agent(model: str = DEFAULT_MODEL) -> "LlmAgent":
    tools = _build_tools()
    return LlmAgent(
        name="jd_agent",
        model=model,
        description="Analyzes a job description into structured role requirements.",
        instruction=JD_AGENT_INSTRUCTION,
        tools=tools["jd"],
        output_key="jd_requirements",
    )


def build_resume_screener_agent(model: str = DEFAULT_MODEL) -> "LlmAgent":
    tools = _build_tools()
    return LlmAgent(
        name="resume_screener_agent",
        model=model,
        description="Parses and scores resumes against the role requirements.",
        instruction=RESUME_AGENT_INSTRUCTION,
        tools=tools["resume"],
        output_key="ranked_candidates",
    )


def build_scheduler_agent(model: str = DEFAULT_MODEL) -> "LlmAgent":
    tools = _build_tools()
    return LlmAgent(
        name="interview_scheduler_agent",
        model=model,
        description="Drafts personalized interview invitations and proposes slots.",
        instruction=SCHEDULER_AGENT_INSTRUCTION,
        tools=tools["scheduler"],
        output_key="interview_emails",
    )


def build_insights_agent(model: str = DEFAULT_MODEL) -> "LlmAgent":
    tools = _build_tools()
    return LlmAgent(
        name="insights_agent",
        model=model,
        description="Aggregates the recruitment funnel into hiring metrics and a report.",
        instruction=INSIGHTS_AGENT_INSTRUCTION,
        tools=tools["insights"],
        output_key="insights_report",
    )


def build_recruitment_pipeline(model: str = DEFAULT_MODEL) -> "SequentialAgent":
    """Compose all four agents into a SequentialAgent (ADK orchestration).

    A SequentialAgent runs its sub-agents in order, threading shared state
    (jd_requirements -> ranked_candidates -> interview_emails -> insights_report)
    through the session. This IS the multi-agent orchestration the capstone
    requires.
    """
    return SequentialAgent(
        name="smart_hr_recruitment_pipeline",
        description="End-to-end recruitment: JD -> screening -> scheduling -> insights.",
        sub_agents=[
            build_jd_agent(model),
            build_resume_screener_agent(model),
            build_scheduler_agent(model),
            build_insights_agent(model),
        ],
    )


__all__ = [
    "ADK_AVAILABLE",
    "DEFAULT_MODEL",
    "build_jd_agent",
    "build_resume_screener_agent",
    "build_scheduler_agent",
    "build_insights_agent",
    "build_recruitment_pipeline",
]
