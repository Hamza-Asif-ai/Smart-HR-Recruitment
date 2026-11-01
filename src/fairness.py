"""Fairness helpers: blind scoring and the four-fifths adverse-impact report.

Blind mode removes candidate names and direct contact details before the resume
is parsed, sent to the extractor, or scored. The score itself never depends on
identity, so name swaps must leave ``match_percent`` unchanged.

The adverse-impact report implements the EEOC four-fifths (80%) rule over
synthetic groups. It reports selection rates and flags any group whose rate is
below 80% of the highest group's rate; it is an evaluation aid, not a legal
determination.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .models import CandidateProfile
from .security import mask_pii

MASK_TOKEN = "[NAME]"
FOUR_FIFTHS = 0.8


def blind_resume_text(text: str, names: Optional[Iterable[str]] = None) -> str:
    """Mask names and contact details while preserving skills/experience text."""
    value = str(text or "")
    for name in names or ():
        clean = str(name or "").strip()
        if len(clean) < 2:
            continue
        value = re.sub(re.escape(clean), MASK_TOKEN, value, flags=re.IGNORECASE)
    return mask_pii(value, mask_names=True)


def blind_profile(profile: CandidateProfile) -> CandidateProfile:
    """Return a copy with identity fields removed before deterministic scoring."""
    return profile.model_copy(update={
        "name": "Candidate",
        "email": "",
        "phone": "",
    })


def _group_counts(values: Any) -> tuple[int, int]:
    if isinstance(values, Mapping):
        selected = int(values.get("selected", 0) or 0)
        total = int(values.get("total", values.get("applicants", 0)) or 0)
        return max(0, min(selected, total)), max(0, total)
    if isinstance(values, (list, tuple, set, frozenset)):
        flags = list(values)
        return sum(1 for flag in flags if flag), len(flags)
    selected = int(values or 0)
    return selected, selected


def selection_rates(groups: Mapping[str, Any]) -> Dict[str, Dict[str, float]]:
    """Return selected/total/rate for every synthetic group."""
    report: Dict[str, Dict[str, float]] = {}
    for name, values in (groups or {}).items():
        selected, total = _group_counts(values)
        report[str(name)] = {
            "selected": selected,
            "total": total,
            "rate": round(selected / total, 4) if total else 0.0,
        }
    return report


def adverse_impact_report(
    groups: Mapping[str, Any],
    *,
    four_fifths: float = FOUR_FIFTHS,
) -> Dict[str, Any]:
    """Compute selection rates and flag groups below the four-fifths threshold."""
    threshold = max(0.0, min(1.0, float(four_fifths)))
    rates = selection_rates(groups)
    populated = {name: data for name, data in rates.items() if data["total"] > 0}
    reference_name = ""
    reference_rate = 0.0
    if populated:
        reference_name, reference_data = max(
            populated.items(), key=lambda item: (item[1]["rate"], item[0])
        )
        reference_rate = reference_data["rate"]

    impacted = []
    for name, data in rates.items():
        ratio = (data["rate"] / reference_rate) if reference_rate > 0 else 1.0
        data["ratio_to_reference"] = round(ratio, 4)
        if data["total"] and ratio < threshold:
            impacted.append(name)

    return {
        "metric": "four_fifths_adverse_impact",
        "four_fifths_threshold": threshold,
        "reference_group": reference_name,
        "reference_rate": reference_rate,
        "groups": rates,
        "impacted_groups": sorted(impacted),
        "passes_4_5": not impacted,
        "interpretation": (
            "passes the four-fifths screening rule"
            if not impacted else
            "one or more groups fall below 80% of the highest selection rate"
        ),
    }


def group_scores_report(
    scores_by_group: Mapping[str, Sequence[float]],
    *,
    threshold: float,
    four_fifths: float = FOUR_FIFTHS,
) -> Dict[str, Any]:
    """Convenience wrapper: convert scores to selections, then run the report."""
    groups = {
        name: [float(score) >= float(threshold) for score in scores]
        for name, scores in (scores_by_group or {}).items()
    }
    report = adverse_impact_report(groups, four_fifths=four_fifths)
    report["selection_threshold"] = float(threshold)
    return report


__all__ = [
    "FOUR_FIFTHS",
    "MASK_TOKEN",
    "adverse_impact_report",
    "blind_profile",
    "blind_resume_text",
    "group_scores_report",
    "selection_rates",
]
