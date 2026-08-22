"""Immutable-report display projection for audited AI narratives.

Provider output and its audit input remain frozen verbatim.  This module only
creates the user-facing copy after an audit has passed, so presentation status
and terminology cannot contradict the audited publication state.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


PRESENTATION_NARRATIVE_VERSION = "ai-report-presentation-narrative-v1"

# These are domain enum values that can legitimately occur in provider prose.
# The presentation boundary must never expose them as implementation tokens.
_ZH_ENUMS = {
    "HH_HL": "更高高点 / 更高低点",
    "LH_LL": "更低高点 / 更低低点",
}

_AUDIT_PENDING_SENTENCE = re.compile(
    r"(?:报告|本报告)(?:尚)?未经审计[，,。；;\s]*(?:审计状态为)?(?:待定|等待审计|PENDING)[。；;\s]*",
    re.IGNORECASE,
)
_LEADING_CONNECTIVE = re.compile(r"^\s*(?:而|但|同时|此外|因此|不过)[，,\s]*")


def _display_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    result = value
    for token, label in _ZH_ENUMS.items():
        # ``_`` is a regex word character, so \b would not match the end of
        # values such as HH_HL.  Treat domain-token characters explicitly.
        result = re.sub(rf"(?<![A-Z0-9_]){re.escape(token)}(?![A-Z0-9_])", label, result)
    return result


def _clean_conclusion(value: Any) -> Any:
    text = _display_text(value)
    if not isinstance(text, str):
        return text
    if _LEADING_CONNECTIVE.match(text):
        text = _LEADING_CONNECTIVE.sub("综合来看，", text, count=1)
    return text


def _clean_limitations(value: Any, audit_status: str) -> Any:
    text = _display_text(value)
    if not isinstance(text, str) or audit_status != "PASSED":
        return text
    text = _AUDIT_PENDING_SENTENCE.sub("", text).strip()
    return re.sub(r"^[，,；;\s]+|[，,；;\s]+$", "", text)


def project_display_narrative(response: dict[str, Any], *, audit_status: str) -> dict[str, Any]:
    """Return a display-only report copy without changing persisted evidence.

    This deliberately runs only at the presentation boundary.  It neither
    changes the immutable provider response nor affects validation/audit hashes.
    """
    projected = deepcopy(response)
    projected["audit_status"] = audit_status
    projected["presentation_narrative_version"] = PRESENTATION_NARRATIVE_VERSION
    projected["headline"] = _display_text(projected.get("headline"))
    for section in projected.get("sections", []):
        if not isinstance(section, dict):
            continue
        section["title"] = _display_text(section.get("title"))
        if section.get("section_id") == "CONCLUSION":
            section["body"] = _clean_conclusion(section.get("body"))
        elif section.get("section_id") == "LIMITATIONS":
            section["body"] = _clean_limitations(section.get("body"), audit_status)
        else:
            section["body"] = _display_text(section.get("body"))
        if isinstance(section.get("uncertainties"), list):
            section["uncertainties"] = [_clean_limitations(item, audit_status)
                                        if section.get("section_id") == "LIMITATIONS"
                                        else _display_text(item)
                                        for item in section["uncertainties"]]
    for key in ("key_levels", "scenarios"):
        for item in projected.get(key, []):
            if not isinstance(item, dict):
                continue
            for field, value in tuple(item.items()):
                if isinstance(value, str):
                    item[field] = _display_text(value)
    return projected
