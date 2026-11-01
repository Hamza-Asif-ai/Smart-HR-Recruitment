"""Per-run state, concurrency limits, and temporary-file hygiene.

The UI is multi-user: nothing here is a process-wide singleton for a *user's*
data. Each session/run creates its own :class:`RunContext` (trace recorder,
budget, and memory). The only shared object is the concurrency gate, which is
intentionally global so the server cannot be overloaded by many sessions.
"""

from __future__ import annotations

import atexit
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from . import config
from .budget import BudgetExceededError, BudgetLimits, LLMBudgetExceeded, RunBudget
from .tracing import TraceRecorder


class QueueFullError(RuntimeError):
    """Raised when the bounded work queue cannot accept another run."""


@dataclass
class RunMemory:
    """Small key/value store scoped to one run."""

    values: Dict[str, Any] = field(default_factory=dict)

    def set(self, key: str, value: Any) -> None:
        self.values[str(key)] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(str(key), default)

    def clear(self) -> None:
        self.values.clear()


@dataclass
class RunContext:
    trace: TraceRecorder = field(default_factory=TraceRecorder)
    budget: RunBudget = field(default_factory=RunBudget)
    memory: RunMemory = field(default_factory=RunMemory)


def new_run_context(*, max_steps: int = 100, max_seconds: Optional[float] = None,
                    max_llm_calls: Optional[int] = None,
                    max_tokens: Optional[int] = None) -> RunContext:
    limits = BudgetLimits(
        max_llm_calls=config.MAX_LLM_CALLS if max_llm_calls is None else max_llm_calls,
        max_seconds=float(config.MAX_SECONDS if max_seconds is None else max_seconds),
        max_tokens=config.MAX_TOKENS if max_tokens is None else max_tokens,
        max_steps=max_steps,
    )
    return RunContext(trace=TraceRecorder(), budget=RunBudget(limits=limits), memory=RunMemory())


class ConcurrencyGate:
    """Bounded waiting room plus a semaphore for active work."""

    def __init__(self, limit: int, max_waiting: int):
        self.limit = max(1, int(limit))
        self.max_waiting = max(1, int(max_waiting))
        self._semaphore = threading.BoundedSemaphore(self.limit)
        self._lock = threading.Lock()
        self._waiting = 0

    @contextmanager
    def slot(self, timeout: float = 30.0) -> Iterator[None]:
        with self._lock:
            if self._waiting >= self.max_waiting:
                raise QueueFullError("server is busy; try again shortly")
            self._waiting += 1
        acquired = False
        try:
            acquired = self._semaphore.acquire(timeout=max(0.1, float(timeout)))
            if not acquired:
                raise QueueFullError("server is busy; try again shortly")
            yield
        finally:
            if acquired:
                self._semaphore.release()
            with self._lock:
                self._waiting -= 1


QUEUE_GATE = ConcurrencyGate(config.QUEUE_CONCURRENCY_LIMIT, config.QUEUE_MAX_SIZE)

_TEMP_PREFIX = "smart_hr_"
_TEMP_LOCK = threading.Lock()
_LIVE_TEMP_FILES: set[str] = set()


def temp_root() -> Path:
    path = Path(tempfile.gettempdir()) / "smart_hr"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_temp_files(max_age_seconds: Optional[int] = None, *,
                       include_live: bool = False) -> int:
    """Delete stale temp files created by this app; return the count removed."""
    max_age = config.TEMP_FILE_MAX_AGE_SECONDS if max_age_seconds is None else max_age_seconds
    cutoff = time.time() - max(0, int(max_age))
    removed = 0
    root = temp_root()
    with _TEMP_LOCK:
        for path in root.glob(_TEMP_PREFIX + "*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    if not include_live and str(path) in _LIVE_TEMP_FILES:
                        continue
                    path.unlink()
                    _LIVE_TEMP_FILES.discard(str(path))
                    removed += 1
            except OSError:
                continue
    return removed


def new_temp_file(suffix: str = "", prefix: str = _TEMP_PREFIX) -> str:
    """Create a uniquely named temp file inside the app's temp directory."""
    cleanup_temp_files()
    fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=str(temp_root()))
    os.close(fd)
    with _TEMP_LOCK:
        _LIVE_TEMP_FILES.add(path)
    return path


def release_temp_file(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
    with _TEMP_LOCK:
        _LIVE_TEMP_FILES.discard(str(path))


def _cleanup_at_exit() -> None:
    # Only remove files this process created; stale files were already swept.
    with _TEMP_LOCK:
        paths = list(_LIVE_TEMP_FILES)
    for path in paths:
        release_temp_file(path)


atexit.register(_cleanup_at_exit)


__all__ = [
    "BudgetExceededError",
    "LLMBudgetExceeded",
    "ConcurrencyGate",
    "QUEUE_GATE",
    "QueueFullError",
    "RunBudget",
    "RunContext",
    "RunMemory",
    "cleanup_temp_files",
    "new_run_context",
    "new_temp_file",
    "release_temp_file",
    "temp_root",
]
