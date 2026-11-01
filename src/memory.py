"""Small, privacy-preserving hiring memory backed by the standard library.

Only an HMAC-SHA256 digest of the normalised candidate email is persisted; the
raw email never reaches SQLite. Memory is advisory: callers may show a hint to
the HR, but must never use it to change a deterministic score.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from . import config
from .security import mask_pii

_DECISION_LABELS = {
    "shortlist": "shortlisted",
    "shortlisted": "shortlisted",
    "approve": "shortlisted",
    "reject": "rejected",
    "rejected": "rejected",
    "decline": "rejected",
    "override": "overridden",
    "interview": "interviewed",
}


class CandidateMemory:
    """SQLite-backed decision memory keyed by HMAC(email)."""

    def __init__(self, db_path: Optional[str] = None, hmac_key: Optional[bytes] = None):
        self.db_path = Path(db_path or config.MEMORY_DB_PATH)
        if hmac_key is None:
            raw_key = (os.environ.get("HR_MEMORY_HMAC_KEY")
                       or os.environ.get("MEMORY_HMAC_KEY")
                       or config.MEMORY_HMAC_KEY)
            if raw_key:
                hmac_key = raw_key.encode("utf-8")
            else:
                hmac_key = secrets.token_bytes(32)
        self._key = hmac_key.encode("utf-8") if isinstance(hmac_key, str) else bytes(hmac_key)
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS candidate_memory (
                    candidate_key TEXT PRIMARY KEY,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    timestamp TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def candidate_key(self, email: str) -> str:
        normalized = (email or "").strip().lower()
        return hmac.new(self._key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()

    def remember_candidate(self, email: str, decision: str, reason: str = "") -> str:
        if not (email or "").strip():
            raise ValueError("email is required")
        key = self.candidate_key(email)
        decision_clean = str(decision).strip().lower()[:40] or "unknown"
        reason_clean = mask_pii(str(reason or ""))[:500]
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO candidate_memory (candidate_key, decision, reason, timestamp)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(candidate_key) DO UPDATE SET
                    decision=excluded.decision,
                    reason=excluded.reason,
                    timestamp=excluded.timestamp
                """,
                (key, decision_clean, reason_clean, timestamp),
            )
            connection.commit()
        return key

    def get_candidate(self, email: str) -> Optional[Dict[str, Any]]:
        key = self.candidate_key(email)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT candidate_key, decision, reason, timestamp "
                "FROM candidate_memory WHERE candidate_key = ?",
                (key,),
            ).fetchone()
        return dict(row) if row else None

    def hint_for(self, email: str) -> Optional[str]:
        """Return a human-readable hint, never a score modifier."""
        row = self.get_candidate(email)
        if not row:
            return None
        label = _DECISION_LABELS.get(row["decision"], row["decision"])
        reason = row.get("reason") or ""
        if label == "rejected":
            text = "HR rejected a similar profile before"
        elif label == "shortlisted":
            text = "HR shortlisted a similar profile before"
        elif label == "overridden":
            text = "HR previously overrode the system recommendation for a similar profile"
        elif label == "interviewed":
            text = "A similar profile was interviewed before"
        else:
            text = f"Previous HR decision: {label}"
        if reason:
            text += f" ({reason})"
        return text

    def forget_candidate(self, email: str) -> bool:
        """Delete the remembered decision for this candidate; return True if removed."""
        key = self.candidate_key(email)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM candidate_memory WHERE candidate_key = ?", (key,)
            )
            connection.commit()
            return cursor.rowcount > 0

    def count(self) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM candidate_memory").fetchone()
        return int(row[0]) if row else 0


__all__ = ["CandidateMemory"]
