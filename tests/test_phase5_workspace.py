"""Phase 5: persistent multi-job workspaces."""

from __future__ import annotations

import json

import pytest

from src.workspace import JobWorkspace, WorkspaceError


def _workspace(tmp_path) -> JobWorkspace:
    return JobWorkspace(str(tmp_path / "workspaces.json"))


def test_create_list_and_persist_across_instances(tmp_path):
    workspace = _workspace(tmp_path)
    created = workspace.create(
        title="Backend Engineer",
        company="Acme",
        required_skills="Python, SQL; Python",
        nice_to_have=["AWS", "docker"],
        min_years="4",
    )

    assert created["title"] == "Backend Engineer"
    assert created["required_skills"] == ["python", "sql"]
    assert created["nice_to_have"] == ["aws", "docker"]
    assert created["min_years"] == 4
    assert workspace.active()["id"] == created["id"]

    reloaded = _workspace(tmp_path)
    jobs = reloaded.list_jobs()
    assert [job["title"] for job in jobs] == ["Backend Engineer"]
    assert reloaded.active()["id"] == created["id"]


def test_update_set_active_and_delete(tmp_path):
    workspace = _workspace(tmp_path)
    first = workspace.create(title="First", make_active=True)
    second = workspace.create(title="Second", company="Beta")

    assert workspace.active()["id"] == second["id"]
    workspace.set_active(first["id"])
    assert workspace.active()["id"] == first["id"]

    updated = workspace.update(first["id"], title="First Updated", min_years=7)
    assert updated["title"] == "First Updated"
    assert updated["min_years"] == 7
    assert updated["company"] == ""

    assert workspace.delete(first["id"]) is True
    assert workspace.active()["id"] == second["id"]
    assert workspace.delete(first["id"]) is False
    assert workspace.delete(second["id"]) is True
    assert workspace.active() is None


def test_missing_and_unknown_job_ids(tmp_path):
    workspace = _workspace(tmp_path)
    assert workspace.get("missing") is None
    with pytest.raises(WorkspaceError):
        workspace.set_active("missing")
    with pytest.raises(WorkspaceError):
        workspace.update("missing", title="Nope")


def test_clear_resets_the_workspace(tmp_path):
    workspace = _workspace(tmp_path)
    workspace.create(title="Role")
    workspace.clear()
    assert workspace.list_jobs() == []
    assert workspace.active() is None


def test_invalid_json_and_oversized_values_are_handled(tmp_path):
    path = tmp_path / "workspaces.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="valid JSON"):
        _workspace(tmp_path).list_jobs()

    path.write_text(json.dumps({"jobs": "nope"}), encoding="utf-8")
    assert _workspace(tmp_path).list_jobs() == []

    workspace = _workspace(tmp_path)
    long_title = "x" * 400
    job = workspace.create(title=long_title, min_years=999, required_skills=["y" * 200])
    assert len(job["title"]) == 160
    assert job["min_years"] == 60
    assert job["required_skills"] == ["y" * 80]


def test_untitled_role_default_and_invite_template_limit(tmp_path):
    workspace = _workspace(tmp_path)
    job = workspace.create(title="   ", invite_template="a" * 25_000)
    assert job["title"] == "Untitled role"
    assert len(job["invite_template"]) == 20_000
