"""Application authentication for the Gradio UI.

Users are configured entirely through the ``HR_USERS`` environment variable so
no credentials are ever written to the repository. Passwords are stored as
PBKDF2-HMAC-SHA256 hashes with a per-user random salt.

Accepted ``HR_USERS`` formats
-----------------------------
1. Delimited records (newlines or semicolons)::

       alice:admin:pbkdf2_sha256$260000$<salt>$<hash>
       bob:recruiter:pbkdf2_sha256$260000$<salt>$<hash>

   ``user:hash:role`` is also accepted. Generate a hash with::

       python -m src.auth --hash-password 'your-password'

2. JSON (object mapping username to a hash/object, or a list of objects)::

       {"alice": {"role": "admin", "hash": "pbkdf2_sha256$..."}}

Roles must be one of :class:`src.security.Role`.

The module deliberately keeps lockout state in memory only. Audit records are
JSON lines and never include passwords or raw exception text. Identifiers are
PII-masked before they are written.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from . import config
from .security import Role, mask_pii, scrub_secrets

PBKDF2_ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = config.AUTH_PBKDF2_ITERATIONS
MIN_ITERATIONS = 100_000
MAX_ITERATIONS = 2_000_000


class AuthConfigError(RuntimeError):
    """Raised when HR_USERS is missing or malformed."""


class AuthenticationError(RuntimeError):
    """Raised when authentication cannot be attempted."""


@dataclass(frozen=True)
class UserRecord:
    username: str
    role: Role
    password_hash: str


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    username: str = ""
    role: Optional[Role] = None
    message: str = ""


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding)


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS,
                  salt: Optional[bytes] = None) -> str:
    """Return a ``pbkdf2_sha256$iterations$salt$hash`` string."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    iterations = int(iterations)
    if not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise ValueError(f"iterations must be between {MIN_ITERATIONS} and {MAX_ITERATIONS}")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PBKDF2_ALGORITHM}${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification of a password against an encoded hash."""
    if not password or not encoded:
        return False
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = str(encoded).split("$", 3)
        if algorithm != PBKDF2_ALGORITHM:
            return False
        iterations = int(iterations_raw)
        if not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
            return False
        salt = _b64decode(salt_raw)
        expected = _b64decode(digest_raw)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", str(password).encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(actual, expected)


def _coerce_role(value: Any) -> Role:
    try:
        return Role(str(value).strip().lower())
    except (ValueError, AttributeError) as exc:
        raise AuthConfigError(f"unknown role {value!r}") from exc


def _user_from_item(username: Any, value: Any) -> UserRecord:
    name = str(username).strip()
    if not name or ":" in name or len(name) > 128:
        raise AuthConfigError("usernames must be 1-128 characters and contain no ':'")
    if isinstance(value, Mapping):
        role_raw = value.get("role", "viewer")
        password_hash = value.get("hash") or value.get("password_hash")
    else:
        role_raw = "viewer"
        password_hash = value
    if not isinstance(password_hash, str) or not password_hash.startswith(PBKDF2_ALGORITHM + "$"):
        raise AuthConfigError(f"user {name!r} does not have a PBKDF2 password hash")
    return UserRecord(username=name, role=_coerce_role(role_raw), password_hash=password_hash)


def parse_users(raw: str) -> Dict[str, UserRecord]:
    """Parse ``HR_USERS`` into a case-insensitive username -> record mapping."""
    raw = (raw or "").strip()
    if not raw:
        return {}

    records: Dict[str, UserRecord] = {}

    if raw.startswith("{") or raw.startswith("["):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AuthConfigError("HR_USERS JSON is invalid") from exc
        items: Iterable[tuple[Any, Any]]
        if isinstance(data, Mapping):
            if isinstance(data.get("users"), (list, Mapping)):
                data = data["users"]
        if isinstance(data, Mapping):
            items = data.items()
        elif isinstance(data, list):
            items = []
            for entry in data:
                if not isinstance(entry, Mapping):
                    raise AuthConfigError("HR_USERS list entries must be objects")
                username = entry.get("username") or entry.get("user")
                items.append((username, entry))
        else:
            raise AuthConfigError("HR_USERS JSON must be an object or list")
        for username, value in items:
            record = _user_from_item(username, value)
            records[record.username.lower()] = record
        return records

    for chunk in raw.replace(";", "\n").splitlines():
        line = chunk.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) != 3:
            raise AuthConfigError(
                "each HR_USERS record must be username:role:pbkdf2_hash"
            )
        username, second, third = (part.strip() for part in parts)
        if second.startswith(PBKDF2_ALGORITHM + "$"):
            password_hash, role_raw = second, third
        elif third.startswith(PBKDF2_ALGORITHM + "$"):
            role_raw, password_hash = second, third
        else:
            raise AuthConfigError(
                f"user {username!r} does not have a PBKDF2 password hash"
            )
        record = UserRecord(
            username=username,
            role=_coerce_role(role_raw),
            password_hash=password_hash,
        )
        records[record.username.lower()] = record
    return records


def load_users() -> Dict[str, UserRecord]:
    return parse_users(os.environ.get("HR_USERS", ""))


class AuthManager:
    """In-memory lockout state plus a PII-free append-only audit log."""

    def __init__(self, users: Mapping[str, UserRecord], *,
                 max_failures: int = config.AUTH_MAX_FAILURES,
                 lockout_seconds: int = config.AUTH_LOCKOUT_SECONDS,
                 audit_path: Optional[str] = None):
        self.users = dict(users)
        self.max_failures = max(1, int(max_failures))
        self.lockout_seconds = max(1, int(lockout_seconds))
        self.audit_path = Path(audit_path or config.AUTH_AUDIT_LOG)
        # Re-entrant: authenticate() holds the lock while _audit() writes.
        self._lock = threading.RLock()
        self._failures: Dict[str, int] = {}
        self._locked_until: Dict[str, float] = {}
        self._dummy_hash = hash_password(secrets.token_urlsafe(24))

    @property
    def enabled(self) -> bool:
        return bool(self.users)

    def _audit(self, event: str, username: str, role: Optional[Role],
               reason: str = "") -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": str(event)[:40],
            "user": mask_pii(str(username))[:128],
            "role": role.value if role else "",
            "reason": mask_pii(scrub_secrets(str(reason)))[:160],
        }
        line = json.dumps(record, ensure_ascii=True)
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                with self.audit_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            # Authentication must not crash because the audit sink is unwritable.
            pass

    def _locked_for(self, key: str) -> int:
        until = self._locked_until.get(key)
        if until is None:
            return 0
        remaining = int(until - time.monotonic())
        if remaining <= 0:
            self._locked_until.pop(key, None)
            self._failures.pop(key, None)
            return 0
        return remaining

    def authenticate(self, username: str, password: str) -> AuthResult:
        name = (username or "").strip()
        key = name.lower()
        if not name or not password:
            self._audit("login_failed", name, None, "missing_credentials")
            return AuthResult(False, message="Invalid username or password.")

        with self._lock:
            remaining = self._locked_for(key)
            if remaining:
                role = self.users.get(key).role if key in self.users else None
                self._audit("login_locked", name, role, "locked_out")
                return AuthResult(
                    False,
                    username=name,
                    message="Account temporarily locked. Try again later.",
                )

            record = self.users.get(key)
            encoded = record.password_hash if record else self._dummy_hash
            valid = verify_password(password, encoded)
            valid = bool(valid and record is not None)

            if valid:
                self._failures.pop(key, None)
                self._locked_until.pop(key, None)
                self._audit("login_success", record.username, record.role)
                return AuthResult(
                    True,
                    username=record.username,
                    role=record.role,
                    message="Signed in.",
                )

            failures = self._failures.get(key, 0) + 1
            self._failures[key] = failures
            role = record.role if record else None
            if failures >= self.max_failures:
                self._locked_until[key] = time.monotonic() + self.lockout_seconds
                self._audit("login_locked", name, role, "too_many_failures")
            else:
                self._audit("login_failed", name, role, "invalid_credentials")
            return AuthResult(
                False,
                username=name,
                message="Invalid username or password.",
            )

    def audit_event(self, event: str, username: str, role: Optional[Role] = None,
                    reason: str = "") -> None:
        """Record a non-login security event (logout, denial, ...)."""
        self._audit(event, username, role, reason)


_AUTH_LOCK = threading.Lock()
_AUTH_MANAGER: Optional[AuthManager] = None


def get_auth_manager(*, reload: bool = False) -> AuthManager:
    """Return the process auth manager, validating startup configuration."""
    global _AUTH_MANAGER
    with _AUTH_LOCK:
        if _AUTH_MANAGER is not None and not reload:
            return _AUTH_MANAGER
        users = load_users()
        if not users:
            allow_no_auth = (
                config.HR_ALLOW_NO_AUTH
                or os.environ.get("HR_ALLOW_NO_AUTH", "").strip() == "1"
            )
            if not allow_no_auth:
                raise AuthConfigError(
                    "HR_USERS is not configured. Set HR_USERS (see SECURITY.md) "
                    "or set HR_ALLOW_NO_AUTH=1 for a local-only, unauthenticated run."
                )
        _AUTH_MANAGER = AuthManager(users)
        return _AUTH_MANAGER


def reset_auth_manager() -> None:
    """Clear the cached manager (used by tests when changing the environment)."""
    global _AUTH_MANAGER
    with _AUTH_LOCK:
        _AUTH_MANAGER = None


def _main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Smart HR authentication helpers")
    parser.add_argument("--hash-password", metavar="PASSWORD",
                        help="print a PBKDF2 hash for HR_USERS")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--check", action="store_true",
                        help="validate HR_USERS without starting the app")
    args = parser.parse_args(argv)

    if args.hash_password is not None:
        password = args.hash_password
        if password == "-":
            password = getpass.getpass("Password: ")
        print(hash_password(password, iterations=args.iterations))
        return 0

    if args.check:
        users = load_users()
        print(f"HR_USERS contains {len(users)} valid user(s).")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "AuthConfigError",
    "AuthManager",
    "AuthResult",
    "AuthenticationError",
    "DEFAULT_ITERATIONS",
    "UserRecord",
    "get_auth_manager",
    "hash_password",
    "load_users",
    "parse_users",
    "reset_auth_manager",
    "verify_password",
]
