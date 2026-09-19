"""
mcp_filesystem_server.py
========================
A minimal **MCP (Model Context Protocol) server** that exposes sandboxed
file-system access to the recruitment agents.

Why MCP? Instead of giving each agent raw, unrestricted disk access, we expose
a small set of audited tools over MCP. Google ADK agents connect to this server
via ``MCPToolset`` and can only:

    * list_resumes()          - enumerate resume files in the data directory
    * read_resume(filename)   - read one resume (path-traversal protected)
    * read_job_description()  - read the active JD
    * save_report(name, text) - persist a generated report to the output dir

Run standalone (stdio transport):

    python -m src.mcp_filesystem_server  --root ./data

ADK then launches it with StdioServerParameters and discovers the tools.

This file degrades gracefully: if the `mcp` package is unavailable (some
Kaggle images), the same functions are importable and callable directly, so
the pipeline keeps working.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import List

# Resolve the data root. Overridable via env var or --root flag.
DATA_ROOT = Path(os.environ.get("HR_DATA_ROOT", "./data")).resolve()
RESUME_DIR = DATA_ROOT / "resumes"
JD_DIR = DATA_ROOT / "job_descriptions"
OUTPUT_DIR = DATA_ROOT / "output"


def _safe_path(base: Path, name: str) -> Path:
    """Resolve ``name`` under ``base`` and reject path traversal."""
    candidate = (base / name).resolve()
    if base not in candidate.parents and candidate != base:
        raise ValueError(f"Access denied: '{name}' is outside the sandbox '{base}'.")
    return candidate


# --------------------------------------------------------------------------- #
# Core file operations (pure functions, independently testable)
# --------------------------------------------------------------------------- #
# Resume formats we know how to read.
SUPPORTED_RESUME_EXTS = {".txt", ".md", ".pdf"}


def list_resumes() -> List[str]:
    """Return the filenames of all supported resumes in the resume directory."""
    if not RESUME_DIR.exists():
        return []
    return sorted(
        p.name for p in RESUME_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_RESUME_EXTS
    )


def _extract_pdf_text(path: Path) -> str:
    """Extract plain text from a PDF resume using pypdf."""
    try:
        from pypdf import PdfReader  # imported lazily so .txt-only runs need no dep
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Reading PDF resumes requires 'pypdf' (pip install pypdf)."
        ) from exc
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def read_resume(filename: str) -> str:
    """Read and return the text of a single resume by filename.

    Transparently handles both plain-text and PDF resumes, so downstream
    skills (``resume_parser_skill`` etc.) receive uniform text regardless of
    the source format.
    """
    path = _safe_path(RESUME_DIR, filename)
    if path.suffix.lower() == ".pdf":
        return _extract_pdf_text(path)
    return path.read_text(encoding="utf-8", errors="ignore")


def read_job_description(filename: str = "") -> str:
    """Read the active job description. If no filename is given, read the first."""
    if not filename:
        files = sorted(p.name for p in JD_DIR.iterdir() if p.is_file())
        if not files:
            return ""
        filename = files[0]
    path = _safe_path(JD_DIR, filename)
    return path.read_text(encoding="utf-8", errors="ignore")


def save_report(name: str, content: str) -> str:
    """Persist ``content`` to the output directory and return its path."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _safe_path(OUTPUT_DIR, name)
    path.write_text(content, encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------- #
# MCP server registration (only when the `mcp` package is present)
# --------------------------------------------------------------------------- #
def build_server():
    """Construct and return a FastMCP server exposing the file tools."""
    from mcp.server.fastmcp import FastMCP  # imported lazily

    mcp = FastMCP("hr-filesystem")

    @mcp.tool()
    def mcp_list_resumes() -> List[str]:
        """List all resume filenames available for screening."""
        return list_resumes()

    @mcp.tool()
    def mcp_read_resume(filename: str) -> str:
        """Read the full text of a resume given its filename."""
        return read_resume(filename)

    @mcp.tool()
    def mcp_read_job_description(filename: str = "") -> str:
        """Read a job description; reads the first JD if filename is empty."""
        return read_job_description(filename)

    @mcp.tool()
    def mcp_save_report(name: str, content: str) -> str:
        """Save a generated report to the output directory; returns the path."""
        return save_report(name, content)

    return mcp


def main() -> None:
    global DATA_ROOT, RESUME_DIR, JD_DIR, OUTPUT_DIR

    parser = argparse.ArgumentParser(description="HR filesystem MCP server")
    parser.add_argument("--root", default=str(DATA_ROOT), help="Data root directory")
    args = parser.parse_args()

    DATA_ROOT = Path(args.root).resolve()
    RESUME_DIR = DATA_ROOT / "resumes"
    JD_DIR = DATA_ROOT / "job_descriptions"
    OUTPUT_DIR = DATA_ROOT / "output"

    server = build_server()
    server.run()  # stdio transport by default


if __name__ == "__main__":
    main()
