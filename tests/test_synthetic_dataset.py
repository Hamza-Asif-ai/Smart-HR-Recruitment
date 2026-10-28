from pathlib import Path

from src import mcp_filesystem_server as fs
from src.sanitize import sanitize_untrusted_text
from tools.check_no_pii import scan_repository

ROOT = Path(__file__).resolve().parents[1]
RESUME_DIR = ROOT / "data" / "resumes"


def test_dataset_has_expected_synthetic_fixtures():
    names = fs.list_resumes()
    assert len(names) == 18
    assert len([n for n in names if n.endswith(".pdf")]) == 3
    assert "resume_scanned_ravi_kumar.pdf" in names
    assert "resume_hidden_white_text_emma_jones.pdf" in names
    assert "resume_injection_ignore_previous_olivia_brown.txt" in names


def test_dataset_contains_only_example_emails_and_555_phones():
    issues = scan_repository(ROOT / "data", scan_all=True)
    assert not issues, "\n".join(f"{i.path}:{i.line} {i.rule} {i.detail}" for i in issues)


def test_pdf_edge_cases():
    normal = fs.read_resume("resume_pdf_sofia_martinez.pdf")
    scanned = fs.read_resume("resume_scanned_ravi_kumar.pdf")
    hidden = fs.read_resume("resume_hidden_white_text_emma_jones.pdf")

    assert len(normal) > 100
    assert scanned == ""
    assert len(hidden) > 0
    assert "ignore previous instructions" in hidden.lower()


def test_injection_text_is_sanitised():
    raw = fs.read_resume("resume_injection_ignore_previous_olivia_brown.txt")
    assert "ignore previous instructions" in raw.lower()

    sanitised = sanitize_untrusted_text(raw)
    assert "<untrusted_data>" in sanitised
    assert "ignore previous instructions" not in sanitised.lower()
    assert "[REDACTED]" in sanitised
