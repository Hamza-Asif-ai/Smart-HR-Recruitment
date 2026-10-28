import logging

import pytest

from src.security import (
    AccessDeniedError,
    PiiMaskingFilter,
    Role,
    SecurityContext,
    mask_pii,
    safe_preview,
)


def test_mask_pii_masks_email_phone_and_url():
    raw = "Ana at ana.silva@example.com or +1 (415) 555-0103, see https://example.com/ana"
    masked = mask_pii(raw)
    assert "ana.silva@example.com" not in masked
    assert "555-0103" not in masked
    assert "[EMAIL]" in masked
    assert "[PHONE]" in masked


def test_mask_pii_can_mask_leading_name():
    masked = mask_pii("DANIEL OKAFOR\nSenior Backend Engineer", mask_names=True)
    assert masked.startswith("[NAME]")


def test_safe_preview_masks_before_truncating():
    preview = safe_preview("nadia.petrova@example.com and +1 (415) 555-0103", limit=20)
    assert "nadia" not in preview
    assert "@example.com" not in preview
    assert "555" not in preview


def test_safe_preview_can_mask_candidate_name():
    preview = safe_preview("Nadia Petrova\nSite Reliability Engineer", mask_names=True)
    assert "Nadia" not in preview
    assert preview.startswith("[NAME]")


def test_logging_filter_masks_records():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="candidate email %s",
        args=("nadia.petrova@example.com",),
        exc_info=None,
    )
    PiiMaskingFilter().filter(record)
    assert "nadia.petrova@example.com" not in record.getMessage()
    assert "[EMAIL]" in record.getMessage()


def test_viewer_is_denied_but_recruiter_is_allowed():
    viewer = SecurityContext(role=Role.VIEWER)
    recruiter = SecurityContext(role=Role.RECRUITER)

    with pytest.raises(AccessDeniedError):
        viewer.authorize("read_resume")

    recruiter.authorize("read_resume")
    assert recruiter.audit_log[-1]["granted"] is True


def test_maybe_mask_respects_role():
    raw = "ana.silva@example.com"
    assert SecurityContext(role=Role.RECRUITER).maybe_mask(raw) == raw
    assert SecurityContext(role=Role.VIEWER).maybe_mask(raw) == "[EMAIL]"
