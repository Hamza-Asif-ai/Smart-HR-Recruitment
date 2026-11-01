"""Phase 5: CSV export for ranked candidates."""

from __future__ import annotations

import csv
import io

from src.exporters import candidates_to_csv, write_candidates_csv


def _candidate(**overrides):
    base = {
        "rank": 1,
        "name": "Nadia Petrova",
        "email": "nadia@example.com",
        "match_percent": 82,
        "recommendation": "Strong match - shortlist",
        "confidence": 0.9,
        "matched_required": ["python", "sql"],
        "missing_required": [],
        "skills": ["python", "sql"],
        "evidence": {
            "skills": [{"skill": "python", "quote": "Built Python services"}],
        },
        "source_id": "resume-1",
        "extraction_mode": "regex",
        "memory_hint": "shortlisted before",
    }
    base.update(overrides)
    return base


def test_csv_has_stable_columns_and_rows():
    text = candidates_to_csv([_candidate()])
    reader = csv.DictReader(io.StringIO(text))
    assert reader.fieldnames[0] == "rank"
    assert "why_not_shortlisted" in reader.fieldnames
    row = next(reader)
    assert row["name"] == "Nadia Petrova"
    assert row["matched_required"] == "python; sql"
    assert "Skill: python: Built Python services" in row["evidence_quotes"]
    assert row["needs_human_review"] == "False"


def test_csv_blocks_formula_injection_and_flattens_newlines():
    text = candidates_to_csv([
        _candidate(
            name="=HYPERLINK(\"http://bad.example\")",
            source_id="+cmd",
            memory_hint="line one\nline two",
        )
    ])
    row = next(csv.DictReader(io.StringIO(text)))
    assert row["name"].startswith("'=")
    assert row["source_id"].startswith("'+")
    assert row["memory_hint"] == "line one line two"


def test_csv_includes_why_not_shortlisted_reason():
    text = candidates_to_csv([
        _candidate(match_percent=40, recommendation="Not a match", missing_required=["aws"])
    ], shortlist_threshold=75)
    row = next(csv.DictReader(io.StringIO(text)))
    assert "below the shortlist threshold" in row["why_not_shortlisted"]
    assert "Missing required skills: aws." in row["why_not_shortlisted"]


def test_write_candidates_csv_uses_utf8_bom_and_creates_parent(tmp_path):
    destination = tmp_path / "nested" / "candidates.csv"
    written = write_candidates_csv([_candidate()], str(destination))
    assert written == str(destination)
    raw = destination.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert "Nadia Petrova" in raw.decode("utf-8-sig")


def test_csv_handles_empty_input():
    text = candidates_to_csv([])
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows == []
