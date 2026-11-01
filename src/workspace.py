"""Persistent multi-job workspace.

Recruiters can keep several job descriptions side by side and switch the active
one in the UI. This module only stores job configuration (title, company,
skills, experience, invitation template) as JSON; it never stores candidate
data, resumes, or credentials.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import config

_WORKSPACE_VERSION = 1


class WorkspaceError(RuntimeError):
    """Workspace file could not be read or written."""


def _clean_text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clean_skills(values: Any, limit: int = 50) -> List[str]:
    if isinstance(values, str):
        raw: Iterable[str] = values.replace(";", ",").replace("\n", ",").split(",")
    elif isinstance(values, (list, tuple, set)):
        raw = values
    else:
        raw = []
    out: List[str] = []
    seen = set()
    for value in raw:
        skill = _clean_text(value, 80).lower()
        if skill and skill not in seen:
            seen.add(skill)
            out.append(skill)
        if len(out) >= limit:
            break
    return out


def _clean_years(value: Any) -> int:
    try:
        return max(0, min(int(value or 0), 60))
    except (TypeError, ValueError):
        return 0


class JobWorkspace:
    """Thread-safe JSON-backed collection of job descriptions."""

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path or config.WORKSPACE_FILE)
        self._lock = threading.RLock()
        self._data: Optional[Dict[str, Any]] = None

    def _empty(self) -> Dict[str, Any]:
        return {"version": _WORKSPACE_VERSION, "active_id": "", "jobs": []}

    def _read(self) -> Dict[str, Any]:
        if self._data is not None:
            return self._data
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._data = self._empty()
            return self._data
        except OSError as exc:
            raise WorkspaceError(f"Could not read the workspace file ({type(exc).__name__}).") from exc
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise WorkspaceError("The workspace file is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise WorkspaceError("The workspace file has an invalid structure.")
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            jobs = []
        clean_jobs = []
        for job in jobs:
            if isinstance(job, dict) and job.get("id") and job.get("title"):
                clean_jobs.append(self._normalise(job, job_id=str(job["id"])))
        active_id = str(payload.get("active_id", "") or "")
        if active_id and not any(job["id"] == active_id for job in clean_jobs):
            active_id = clean_jobs[0]["id"] if clean_jobs else ""
        self._data = {"version": _WORKSPACE_VERSION, "active_id": active_id, "jobs": clean_jobs}
        return self._data

    def _normalise(self, values: Dict[str, Any], *, job_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "id": str(job_id or values.get("id") or uuid.uuid4().hex),
            "title": _clean_text(values.get("title"), 160) or "Untitled role",
            "company": _clean_text(values.get("company"), 160),
            "required_skills": _clean_skills(values.get("required_skills")),
            "nice_to_have": _clean_skills(values.get("nice_to_have")),
            "min_years": _clean_years(values.get("min_years")),
            "invite_template": str(values.get("invite_template") or "")[:20_000],
        }

    def _write(self) -> None:
        if self._data is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._data, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            raise WorkspaceError(f"Could not save the workspace file ({type(exc).__name__}).") from exc

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._read()["jobs"])

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for job in self._read()["jobs"]:
                if job["id"] == str(job_id):
                    return copy.deepcopy(job)
        return None

    def active(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._read()
            return self.get(data.get("active_id", ""))

    def set_active(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            data = self._read()
            if not any(job["id"] == str(job_id) for job in data["jobs"]):
                raise WorkspaceError("That job does not exist.")
            data["active_id"] = str(job_id)
            self._write()
            return self.get(job_id)  # type: ignore[return-value]

    def create(
        self,
        *,
        title: str,
        company: str = "",
        required_skills: Any = None,
        nice_to_have: Any = None,
        min_years: Any = 0,
        invite_template: str = "",
        make_active: bool = True,
    ) -> Dict[str, Any]:
        job = self._normalise({
            "title": title,
            "company": company,
            "required_skills": required_skills,
            "nice_to_have": nice_to_have,
            "min_years": min_years,
            "invite_template": invite_template,
        })
        with self._lock:
            data = self._read()
            data["jobs"].append(job)
            if make_active or not data.get("active_id"):
                data["active_id"] = job["id"]
            self._write()
        return copy.deepcopy(job)

    def update(self, job_id: str, **fields: Any) -> Dict[str, Any]:
        with self._lock:
            data = self._read()
            for index, job in enumerate(data["jobs"]):
                if job["id"] != str(job_id):
                    continue
                merged = {**job, **fields, "id": job["id"]}
                updated = self._normalise(merged, job_id=job["id"])
                data["jobs"][index] = updated
                self._write()
                return copy.deepcopy(updated)
        raise WorkspaceError("That job does not exist.")

    def delete(self, job_id: str) -> bool:
        with self._lock:
            data = self._read()
            remaining = [job for job in data["jobs"] if job["id"] != str(job_id)]
            if len(remaining) == len(data["jobs"]):
                return False
            data["jobs"] = remaining
            if data.get("active_id") == str(job_id):
                data["active_id"] = remaining[0]["id"] if remaining else ""
            self._write()
            return True

    def clear(self) -> None:
        with self._lock:
            self._data = self._empty()
            self._write()


__all__ = ["JobWorkspace", "WorkspaceError"]
