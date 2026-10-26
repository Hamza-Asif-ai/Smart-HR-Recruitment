"""
budget.py
=========
Budget tracking for LLM calls and time.
"""

from __future__ import annotations

import time
import threading
from typing import Any


class BudgetExceeded(Exception):
    """Raised when budget is exceeded."""
    pass


class BudgetTracker:
    """Track LLM calls and execution time."""
    
    def __init__(self, max_llm_calls: int = 50, max_seconds: int = 300):
        self.max_llm_calls = max_llm_calls
        self.max_seconds = max_seconds
        self._lock = threading.Lock()
        self.llm_calls = 0
        self.start_time = time.time()
    
    def check_budget(self) -> None:
        """Check if budget exceeded. Raises BudgetExceeded if so."""
        with self._lock:
            elapsed = time.time() - self.start_time
            if self.llm_calls >= self.max_llm_calls:
                raise BudgetExceeded(
                    f"LLM call budget exceeded: {self.llm_calls}/{self.max_llm_calls}"
                )
            if elapsed >= self.max_seconds:
                raise BudgetExceeded(
                    f"Time budget exceeded: {elapsed:.1f}s/{self.max_seconds}s"
                )
    
    def increment_llm_calls(self) -> None:
        """Increment LLM call counter."""
        with self._lock:
            self.llm_calls += 1
    
    def reset(self) -> None:
        """Reset budget tracker."""
        with self._lock:
            self.llm_calls = 0
            self.start_time = time.time()
    
    def get_status(self) -> dict:
        """Get budget status."""
        with self._lock:
            elapsed = time.time() - self.start_time
            return {
                "llm_calls": self.llm_calls,
                "max_llm_calls": self.max_llm_calls,
                "seconds_elapsed": round(elapsed, 1),
                "max_seconds": self.max_seconds,
            }


# Module-level singleton
budget_tracker = BudgetTracker()


__all__ = ["BudgetTracker", "BudgetExceeded", "budget_tracker"]
