from pathlib import Path

from tools.check_no_pii import scan_repository, scan_text

ROOT = Path(__file__).resolve().parents[1]


def _rules(text: str) -> set[str]:
    return {issue.rule for issue in scan_text(text, "input")}


def test_repository_has_no_pii_or_secrets():
    issues = scan_repository(ROOT, scan_all=True)
    assert not issues, "\n".join(f"{i.path}:{i.line} {i.rule} {i.detail}" for i in issues)


def test_allowed_example_email_passes():
    assert _rules("contact ana.silva@example.com") == set()


def test_non_example_email_fails():
    bad = "person" + "@" + "gmail.com"
    assert "email" in _rules(f"contact {bad}")


def test_phone_with_555_passes():
    assert _rules("call +1 (415) 555-0103") == set()


def test_phone_without_555_fails():
    bad = "+1 (212) " + "867-" + "5309"
    assert "phone" in _rules(f"call {bad}")


def test_iso_date_is_not_treated_as_phone():
    assert _rules("deadline 2026-06-30") == set()


def test_svg_path_data_is_not_treated_as_phone():
    svg = '<svg viewBox="0 0 24 24"><path d="M20.447 20.452h-3.554v-5.569z"/></svg>'
    assert _rules(svg) == set()
    assert "phone" in _rules('call +1 (212) ' + "867-" + "5309")


def test_secret_patterns_fail():
    fake_google = "AIza" + "A" * 35
    fake_github = "ghp_" + "a" * 36
    assert "secret" in _rules(f"key={fake_google}")
    assert "secret" in _rules(f"token={fake_github}")


def test_placeholder_secret_passes():
    assert _rules('GOOGLE_API_KEY="your-key-here"') == set()
