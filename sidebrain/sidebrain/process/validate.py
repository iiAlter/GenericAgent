"""Validate and normalize extracted memory payloads before writing."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sidebrain.paths import QUARANTINE
from sidebrain.process.writer import _safe_filename_part

ALLOWED_STATUS = {"active", "resolved", "superseded", "archived"}


def _string_value(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _string_list(value: Any, field: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if not isinstance(value, list):
        errors.append(f"{field} must be a string or list")
        return []

    result: list[str] = []
    for idx, item in enumerate(value):
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                result.append(stripped)
            continue
        if item is None:
            continue
        errors.append(f"{field}[{idx}] must be a string")
    return result


def _decision_list(value: Any, errors: list[str]) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append("decisions must be a list")
        return []

    decisions: list[dict[str, Any]] = []
    for idx, item in enumerate(value):
        if isinstance(item, str):
            decision = item.strip()
            if decision:
                decisions.append({"decision": decision})
            continue
        if not isinstance(item, dict):
            errors.append(f"decisions[{idx}] must be a string or object")
            continue

        decision = _string_value(item.get("decision"))
        if not decision:
            errors.append(f"decisions[{idx}].decision is required")
            continue

        normalized: dict[str, Any] = {"decision": decision}
        for key in ("reasoning", "alternatives"):
            if item.get(key) is None:
                continue
            if key == "alternatives":
                normalized[key] = _string_list(item.get(key), f"decisions[{idx}].alternatives", errors)
            else:
                text = _string_value(item.get(key))
                if text:
                    normalized[key] = text
                else:
                    errors.append(f"decisions[{idx}].{key} must be a string")
        decisions.append(normalized)
    return decisions


def normalize_extraction(data: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Return normalized extraction data, or validation errors.

    The GA task output is intentionally treated as untrusted data. This function
    accepts a small amount of benign shape drift, while rejecting records that
    would make long-term memory hard to search or manage.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return None, ["extraction must be a JSON object"]

    summary = _string_value(data.get("summary"))
    if not summary:
        errors.append("summary is required")

    normalized: dict[str, Any] = {
        "summary": summary or "",
        "key_points": _string_list(data.get("key_points"), "key_points", errors),
        "action_items": _string_list(data.get("action_items"), "action_items", errors),
        "decisions": _decision_list(data.get("decisions"), errors),
        "people_mentioned": _string_list(data.get("people_mentioned"), "people_mentioned", errors),
        "projects_mentioned": _string_list(data.get("projects_mentioned"), "projects_mentioned", errors),
        "tags": _string_list(data.get("tags"), "tags", errors),
        "related": _string_list(data.get("related"), "related", errors),
        "supersedes": _string_list(data.get("supersedes"), "supersedes", errors),
    }

    for field in ("type", "topic_key", "original_text"):
        value = data.get(field)
        if value is None:
            continue
        text = _string_value(value)
        if text:
            normalized[field] = text
        else:
            errors.append(f"{field} must be a non-empty string")

    status = data.get("status")
    if status is not None:
        status_text = _string_value(status)
        if not status_text:
            errors.append("status must be a non-empty string")
        elif status_text not in ALLOWED_STATUS:
            errors.append(f"status must be one of {sorted(ALLOWED_STATUS)}")
        else:
            normalized["status"] = status_text

    confidence = data.get("confidence")
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            errors.append("confidence must be a number between 0 and 1")
        elif confidence < 0 or confidence > 1:
            errors.append("confidence must be between 0 and 1")
        else:
            normalized["confidence"] = float(confidence)

    if errors:
        return None, errors
    return normalized, []


def write_validation_quarantine(
    source_id: str,
    source_path: str,
    raw_payload: str,
    errors: list[str],
    quarantine_dir: Path | None = None,
) -> Path:
    """Write invalid extraction payload details to quarantine."""
    target_dir = quarantine_dir or QUARANTINE
    target_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d%H%M%S")
    safe_source_id = _safe_filename_part(source_id, default="unknown", max_len=80)
    q_file = target_dir / f"validation__{safe_source_id}__{ts}.json"
    record = {
        "source_id": source_id,
        "source_path": source_path,
        "errors": errors,
        "raw": raw_payload,
    }
    q_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return q_file
