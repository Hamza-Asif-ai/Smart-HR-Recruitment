"""
tracing.py
==========
Trace recorder for agent observability.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Dict, List


class TraceRecorder:
    """Thread-safe trace recorder singleton."""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._traces: List[Dict[str, Any]] = []
    
    def record(self, agent: str, action: str, tool: str = "", 
               input_summary: str = "", output_summary: str = "",
               duration_ms: float = 0.0) -> None:
        """Record a trace entry.
        
        Args:
            agent: Name of agent
            action: Action performed
            tool: Tool used (if any)
            input_summary: Summary of input
            output_summary: Summary of output
            duration_ms: Duration in milliseconds
        """
        entry = {
            "agent": agent,
            "action": action,
            "tool": tool,
            "input_summary": str(input_summary)[:200],
            "output_summary": str(output_summary)[:200],
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "duration_ms": round(duration_ms, 2),
        }
        with self._lock:
            self._traces.append(entry)
    
    def get_traces(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent traces (most recent first)."""
        with self._lock:
            return list(reversed(self._traces[-limit:]))
    
    def clear(self) -> None:
        """Clear all traces."""
        with self._lock:
            self._traces.clear()
    
    def to_json(self) -> str:
        """Export traces as JSON."""
        return json.dumps(self.get_traces(1000), indent=2)


# Module-level singleton
trace_recorder = TraceRecorder()


__all__ = ["TraceRecorder", "trace_recorder"]
