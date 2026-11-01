"""
tracing.py
==========
Trace recorder for agent observability.

A recorder is created per UI session/run (:class:`src.runtime.RunContext`).
There is intentionally no module-level recorder: a shared singleton would mix
one user's trace with another user's.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


class TraceRecorder:
    """Thread-safe, bounded, PII-masked trace recorder for one run."""

    def __init__(self, max_entries: int = 1000, run_id: Optional[str] = None):
        self._lock = threading.Lock()
        self._traces: List[Dict[str, Any]] = []
        self.max_entries = max(1, int(max_entries))
        self.run_id = run_id or uuid.uuid4().hex

    def record(self, agent: str, action: str, tool: str = "",
               input_summary: str = "", output_summary: str = "",
               duration_ms: float = 0.0) -> None:
        """Record a trace entry."""
        from .security import mask_pii, scrub_secrets

        entry = {
            "run_id": self.run_id,
            "agent": str(agent)[:80],
            "action": str(action)[:80],
            "tool": str(tool)[:80],
            "input_summary": mask_pii(scrub_secrets(str(input_summary)))[:200],
            "output_summary": mask_pii(scrub_secrets(str(output_summary)))[:200],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round(float(duration_ms), 2),
        }
        with self._lock:
            self._traces.append(entry)
            if len(self._traces) > self.max_entries:
                del self._traces[:-self.max_entries]

    def get_traces(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent traces (most recent first)."""
        with self._lock:
            return list(reversed(self._traces[-limit:]))

    def clear(self) -> None:
        """Clear all traces."""
        with self._lock:
            self._traces.clear()

    @contextmanager
    def span(self, agent: str, action: str, tool: str = "",
             input_summary: str = "") -> Iterator[None]:
        """Record one bounded span with its real elapsed duration."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(agent, action, tool=tool, input_summary=input_summary,
                        duration_ms=(time.perf_counter() - started) * 1000)

    def export_run(self, path: Optional[str] = None) -> str:
        """Write all traces for this run to JSON and return the path."""
        if path is None:
            from .runtime import new_temp_file

            path = new_temp_file(suffix=".json")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")
        return str(target)

    def to_json(self) -> str:
        """Export traces as JSON."""
        return json.dumps(self.get_traces(1000), indent=2)


def new_trace_recorder(max_entries: int = 1000, run_id: Optional[str] = None) -> TraceRecorder:
    """Create a recorder for one session; never use a module-level singleton."""
    return TraceRecorder(max_entries=max_entries, run_id=run_id)


__all__ = ["TraceRecorder", "new_trace_recorder"]
