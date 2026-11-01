"""CSV export for ranked candidate results."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .scorecard import build_scorecard

_CSV_COLUMNS = [
    "rank",
    "name",
    "email",
    "match_percent",
    "recommendation",
    "confidence",
    "needs_human_review",
    "experience_years",
    "min_years",
    "matched_required",
    "missing_required",
    "skills",
    "evidence_quotes",
    "why_not_shortlisted",
    "source_id",
    "extraction_mode",
    "memory_hint",
]


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    if text.startswith(("=", "+", "-", "@", "\t")):
        text = "'" + text
    return text


def _join(values: Any) -> str:
    if isinstance(values, (list, tuple, set)):
        return "; ".join(str(value) for value in values if value)
    return str(values or "")


def _evidence_text(card: Dict[str, Any]) -> str:
    return " | ".join(
        f"{item.get('label', '')}: {item.get('quote', '')}"
        for item in card.get("evidence", [])
        if item.get("quote")
    )


def candidates_to_csv(
    candidates: Iterable[Dict[str, Any]],
    *,
    shortlist_threshold: Optional[float] = None,
) -> str:
    """Return candidate results as an Excel-friendly CSV string."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for candidate in candidates or []:
        card = build_scorecard(candidate, shortlist_threshold=shortlist_threshold)
        writer.writerow({
            "rank": _cell(candidate.get("rank", "")),
            "name": _cell(card["name"]),
            "email": _cell(card["email"]),
            "match_percent": _cell(candidate.get("match_percent", "")),
            "recommendation": _cell(card["recommendation"]),
            "confidence": _cell(candidate.get("confidence", candidate.get("_confidence", ""))),
            "needs_human_review": _cell(bool(candidate.get("needs_human_review"))),
            "experience_years": _cell(candidate.get("experience_years", "")),
            "min_years": _cell(candidate.get("_min_years", candidate.get("min_years", ""))),
            "matched_required": _cell(_join(candidate.get("matched_required"))),
            "missing_required": _cell(_join(candidate.get("missing_required"))),
            "skills": _cell(_join(candidate.get("skills"))),
            "evidence_quotes": _cell(_evidence_text(card)),
            "why_not_shortlisted": _cell(" | ".join(card["why_not_shortlisted"])),
            "source_id": _cell(candidate.get("source_id", "")),
            "extraction_mode": _cell(candidate.get("extraction_mode", "")),
            "memory_hint": _cell(candidate.get("memory_hint", "")),
        })
    return buffer.getvalue()


def write_candidates_csv(
    candidates: Iterable[Dict[str, Any]],
    path: str,
    *,
    shortlist_threshold: Optional[float] = None,
) -> str:
    """Write the CSV atomically and return the destination path."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        candidates_to_csv(candidates, shortlist_threshold=shortlist_threshold),
        encoding="utf-8-sig",
        newline="",
    )
    return str(destination)


__all__ = ["candidates_to_csv", "write_candidates_csv"]
