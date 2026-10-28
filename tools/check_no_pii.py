"""Fail if the repository contains real PII or committed secrets.

Policy
------
* Email addresses must use ``@example.com`` (the reserved example domain).
* Phone-like sequences must contain the reserved ``555`` exchange.
* Common credential formats (Google, OpenAI, GitHub, AWS, Slack, private keys)
  must never be committed.

Usage
-----
    python tools/check_no_pii.py            # scan files tracked by git
    python tools/check_no_pii.py --all      # scan the working tree too

The checker never prints the matched value; it prints the rule, file, and line.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

ALLOWED_EMAIL_DOMAINS = {"example.com"}
ALLOWED_PHONE_TOKEN = "555"

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "logs",
    "build",
    "dist",
    ".idea",
    ".vscode",
}

SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".bmp",
    ".tiff",
    ".zip",
    ".gz",
    ".tar",
    ".whl",
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".exe",
    ".bin",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".mp3",
    ".mp4",
    ".mov",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
}

TEXT_NAME_HINTS = {
    ".env.example",
    ".gitignore",
    ".gitattributes",
    "Dockerfile",
    "Makefile",
    "Procfile",
}

MAX_TEXT_BYTES = 3_000_000

EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+))(?![\w-])")
PHONE_RE = re.compile(r"(?<![\w])(?:\+?\d[\d\s().-]{6,}\d)(?![\w])")

SECRET_PATTERNS = [
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    (
        "Inline GOOGLE_API_KEY",
        re.compile(r"GOOGLE_API_KEY\s*=\s*['\"]?([A-Za-z0-9_-]{12,})"),
    ),
    (
        "Inline BROWSER_STATE_SECRET",
        re.compile(r"BROWSER_STATE_SECRET\s*=\s*['\"]?([A-Za-z0-9_-]{12,})"),
    ),
]


@dataclass(frozen=True)
class Issue:
    path: str
    line: int
    rule: str
    detail: str = ""


def _is_allowed_email(domain: str) -> bool:
    domain = domain.lower().rstrip(".")
    return any(domain == allowed or domain.endswith("." + allowed) for allowed in ALLOWED_EMAIL_DOMAINS)


def _is_phone_like(match: str) -> bool:
    digits = re.sub(r"\D", "", match)
    if not (9 <= len(digits) <= 15):
        return False
    # Skip ISO dates such as 2026-06-30, which would otherwise look phone-ish.
    if re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", match.strip()):
        return False
    # Skip PDF cross-reference offsets such as "0000000000 65535 f".
    if re.fullmatch(r"0{5,}\s+\d{5}", match.strip()):
        return False
    return True


def _is_placeholder_secret(value: str) -> bool:
    lowered = value.lower()
    markers = (
        "your-key",
        "your_key",
        "your-secret",
        "your_secret",
        "key-here",
        "secret-here",
        "placeholder",
        "changeme",
        "replace-me",
        "example",
        "xxxx",
    )
    return any(marker in lowered for marker in markers) or value.startswith("<")


def scan_text(text: str, label: str, start_line: int = 1) -> Iterator[Issue]:
    """Yield issues from a block of text."""
    for match in EMAIL_RE.finditer(text):
        email = match.group(1)
        domain = match.group(2)
        if not _is_allowed_email(domain):
            yield Issue(label, _line_of(text, match.start(), start_line), "email", f"non-example domain {domain}")

    for match in PHONE_RE.finditer(text):
        value = match.group(0)
        if not _is_phone_like(value):
            continue
        if ALLOWED_PHONE_TOKEN not in re.sub(r"\D", "", value):
            yield Issue(label, _line_of(text, match.start(), start_line), "phone", "missing 555 exchange")

    for rule, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if rule.startswith("Inline ") and match.lastindex and _is_placeholder_secret(match.group(1)):
                continue
            yield Issue(label, _line_of(text, match.start(), start_line), "secret", rule)


def _line_of(text: str, offset: int, start_line: int) -> int:
    return start_line + text.count("\n", 0, offset)


def _decode(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > MAX_TEXT_BYTES:
        return None
    if b"\x00" in raw[:4096]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def _iter_pdf_text(path: Path) -> Iterator[tuple[str, int]]:
    try:
        from pypdf import PdfReader
    except Exception:
        return
    try:
        reader = PdfReader(str(path))
    except Exception:
        return
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        if text:
            yield text, page_number


def iter_candidate_files(root: Path, scan_all: bool) -> Iterator[Path]:
    if scan_all:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
                continue
            if path.name in {".env"} or path.name.startswith(".env.") and path.name != ".env.example":
                continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            yield path
        return

    try:
        out = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    except (OSError, subprocess.CalledProcessError):
        print("warning: git unavailable; falling back to working-tree scan", file=sys.stderr)
        yield from iter_candidate_files(root, scan_all=True)
        return
    for raw in out.split(b"\0"):
        if not raw:
            continue
        rel = os.fsdecode(raw)
        path = root / rel
        if not path.is_file():
            continue
        if path.name == ".env":
            yield path
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def scan_file(path: Path, root: Path) -> Iterator[Issue]:
    rel = path.relative_to(root).as_posix()
    if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
        yield Issue(rel, 1, "secret", "env file must not be committed")
        return
    if path.suffix.lower() == ".pdf":
        for text, page in _iter_pdf_text(path):
            yield from scan_text(text, f"{rel}:page {page}")
        return
    text = _decode(path)
    if text is None:
        return
    yield from scan_text(text, rel)


def scan_repository(root: Path, scan_all: bool = False) -> list[Issue]:
    issues: list[Issue] = []
    for path in iter_candidate_files(root, scan_all):
        issues.extend(scan_file(path, root))
    return issues


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan a repository for real PII and committed secrets.")
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--all", action="store_true", help="scan untracked/ignored working-tree files too")
    parser.add_argument("--quiet", action="store_true", help="only print on failure")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(args.root).resolve()
    issues = scan_repository(root, scan_all=args.all)
    if not issues:
        if not args.quiet:
            print(f"OK: no real PII or committed secrets found under {root}")
        return 0

    print(f"FAIL: {len(issues)} potential PII/secret issue(s):", file=sys.stderr)
    for issue in issues:
        detail = f" ({issue.detail})" if issue.detail else ""
        print(f"  {issue.path}:{issue.line}: {issue.rule}{detail}", file=sys.stderr)
    print("\nUse synthetic values only: @example.com emails and 555 phone numbers.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
