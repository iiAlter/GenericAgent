"""写入引擎 — 将处理后的结构化数据写入 processed/ 目录。

生成带 frontmatter 的 Markdown 文件，包含 schema 校验。
写入失败的文件进入 quarantine。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from sidebrain.paths import PROCESSED, QUARANTINE
from sidebrain.process.dedup import check_duplicate

logger = logging.getLogger(__name__)


def _generate_id() -> str:
    """生成稳定的 UUID。"""
    return str(uuid.uuid4())


def _compute_content_hash(data: dict) -> str:
    """计算结构化数据的 SHA-256 哈希。"""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _make_frontmatter(
    source: str,
    source_path: str,
    source_id: str,
    content_hash: str,
    project: str | None = None,
    memory_type: str | None = None,
    status: str | None = None,
    topic_key: str | None = None,
    tags: list[str] | None = None,
    confidence: float | None = None,
    related: list[str] | None = None,
    supersedes: list[str] | None = None,
) -> dict[str, Any]:
    """生成标准化的 frontmatter。"""
    today = time.strftime("%Y-%m-%d")

    frontmatter: dict[str, Any] = {
        "schema_version": 1,
        "id": _generate_id(),
        "created": today,
        "updated": today,
        "source": source,
        "source_path": source_path,
        "source_id": source_id,
        "content_hash": content_hash,
        "type": memory_type or "note",
        "status": status or "active",
    }

    if project:
        frontmatter["project"] = project
    if topic_key:
        frontmatter["topic_key"] = topic_key
    if tags:
        frontmatter["tags"] = tags
    if confidence is not None:
        frontmatter["confidence"] = round(confidence, 2)
    if related:
        frontmatter["related"] = related
    if supersedes:
        frontmatter["supersedes"] = supersedes

    return frontmatter


def _format_markdown(frontmatter: dict, body: dict) -> str:
    """格式化为带 JSON frontmatter 的 Markdown。

    格式：
    ---{...}---
    body content
    """
    fm_json = json.dumps(frontmatter, ensure_ascii=False, indent=2)
    body_json = json.dumps(body, ensure_ascii=False, indent=2)

    lines = [
        f"---{fm_json}---",
        "",
        "```json",
        body_json,
        "```",
        "",
    ]

    # 如果有原始文本，追加在末尾
    original = body.get("original_text", "")
    if original:
        lines.append("---")
        lines.append("")
        lines.append(original)

    return "\n".join(lines)


def _extract_frontmatter(text: str) -> dict[str, Any] | None:
    match = re.search(r"^---\s*(\{.*?\})\s*---\s*", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _find_active_topic(topic_key: str) -> tuple[Path, dict[str, Any]] | None:
    if not topic_key or not PROCESSED.exists():
        return None

    for f in PROCESSED.rglob("*.md"):
        try:
            frontmatter = _extract_frontmatter(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not frontmatter:
            continue
        if frontmatter.get("topic_key") == topic_key and frontmatter.get("status", "active") == "active":
            return f, frontmatter
    return None


def _validate_frontmatter(fm: dict) -> list[str]:
    """校验 frontmatter 的必填字段。

    Returns:
        缺失字段列表，空列表表示校验通过。
    """
    required = ["schema_version", "id", "created", "updated", "source", "source_path", "source_id", "content_hash"]
    missing = [f for f in required if f not in fm]

    # id 格式校验（UUID）
    if "id" in fm and not re.match(r"^[a-f0-9\-]{32,}$", fm["id"]):
        missing.append("id (invalid UUID format)")

    # content_hash 格式校验
    if "content_hash" in fm and not re.match(r"^[a-f0-9]{64}$", fm["content_hash"]):
        missing.append("content_hash (invalid SHA256)")

    return missing


def _atomic_write(target: Path, content: str) -> bool:
    """原子写入文件。"""
    try:
        tmp = target.with_suffix(".md.tmp")
        tmp.write_text(content, encoding="utf-8")
        fd = os.open(tmp, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        tmp.replace(target)
        return True
    except Exception as e:
        logger.error("Atomic write failed: %s", e)
        return False


def _safe_filename_part(value: str, default: str = "untitled", max_len: int = 80) -> str:
    """Make a string safe for a single filename segment."""
    safe = re.sub(r"[^\w\- ]", "_", value, flags=re.UNICODE).strip(" _-")
    safe = safe.replace(" ", "_")
    return (safe or default)[:max_len]


def _move_to_quarantine(source_id: str, reason: str, content: str) -> None:
    """将失败的内容移入隔离区。"""
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d%H%M%S")
    safe_source_id = _safe_filename_part(source_id, default="unknown", max_len=80)
    safe_reason = _safe_filename_part(reason, default="error", max_len=40)
    q_file = QUARANTINE / f"process__{safe_source_id}__{ts}__{safe_reason}.md"
    try:
        q_file.write_text(content, encoding="utf-8")
        logger.info("Moved to quarantine: %s", q_file.name)
    except Exception as e:
        logger.error("Failed to write quarantine: %s", e)


def _infer_project(source_path: str) -> str | None:
    """从路径推断项目名。"""
    parts = Path(source_path).parts
    for part in parts:
        if part.startswith("--") or part == "pi":
            continue
        if part and not part.startswith("."):
            return part
    return None


def write_processed(
    extracted: dict[str, Any],
    source: str,
    source_path: str,
    source_id: str,
    project: str | None = None,
) -> dict[str, Any]:
    """将提取的结构化数据写入 processed/。

    Args:
        extracted: LLM 提取结果。
        source: 来源类型（pi_session / meeting / ad_hoc）。
        source_path: 原始文件路径（相对）。
        source_id: 来源标识。
        project: 可选的项目名。

    Returns:
        写入结果：
        {
            "success": bool,
            "path": str | None,
            "id": str | None,
            "error": str | None,
        }
    """
    PROCESSED.mkdir(parents=True, exist_ok=True)

    # 计算内容哈希
    body_data = {
        "summary": extracted.get("summary", ""),
        "key_points": extracted.get("key_points", []),
        "action_items": extracted.get("action_items", []),
        "decisions": extracted.get("decisions", []),
        "people_mentioned": extracted.get("people_mentioned", []),
        "projects_mentioned": extracted.get("projects_mentioned", []),
        "original_text": extracted.get("original_text", ""),
    }
    content_hash = _compute_content_hash(body_data)
    topic_key = extracted.get("topic_key")

    existing_topic = _find_active_topic(topic_key) if topic_key else None
    if existing_topic:
        target, frontmatter = existing_topic
        today = time.strftime("%Y-%m-%d")
        frontmatter.update({
            "updated": today,
            "source": source,
            "source_path": source_path,
            "source_id": source_id,
            "content_hash": content_hash,
            "type": extracted.get("type", frontmatter.get("type", "note")),
            "status": extracted.get("status", frontmatter.get("status", "active")),
        })
        for key in ("tags", "confidence", "related", "supersedes"):
            if extracted.get(key) is not None:
                frontmatter[key] = extracted[key]
        if project is not None:
            frontmatter["project"] = project
        elif frontmatter.get("project") is None:
            inferred = _infer_project(source_path)
            if inferred:
                frontmatter["project"] = inferred

        content = _format_markdown(frontmatter, body_data)
        success = _atomic_write(target, content)
        return {
            "success": success,
            "path": str(target) if success else None,
            "id": frontmatter.get("id"),
            "error": None if success else "write_error",
            "updated": success,
            "topic_key": topic_key,
        }

    duplicate = check_duplicate(content_hash)
    if duplicate:
        logger.info("Exact duplicate found, skip write: %s", duplicate.get("path"))
        frontmatter = duplicate.get("frontmatter", {})
        return {
            "success": True,
            "path": duplicate.get("path"),
            "id": frontmatter.get("id"),
            "error": None,
            "duplicate": True,
        }

    # 推断项目
    if project is None:
        project = _infer_project(source_path)

    # 构建 frontmatter
    frontmatter = _make_frontmatter(
        source=source,
        source_path=source_path,
        source_id=source_id,
        content_hash=content_hash,
        project=project,
        memory_type=extracted.get("type"),
        status=extracted.get("status"),
        topic_key=topic_key,
        tags=extracted.get("tags", []),
        confidence=extracted.get("confidence"),
        related=extracted.get("related"),
        supersedes=extracted.get("supersedes"),
    )

    # 校验
    missing = _validate_frontmatter(frontmatter)
    if missing:
        error_msg = f"Schema validation failed: missing {missing}"
        logger.error(error_msg)
        content = _format_markdown(frontmatter, body_data)
        _move_to_quarantine(source_id, "validation_failed", content)
        return {"success": False, "path": None, "id": frontmatter.get("id"), "error": error_msg}

    # 确定目标路径
    tags = extracted.get("tags", [])
    first_tag = tags[0] if tags else (project or "_uncategorized")
    safe_tag = re.sub(r"[^\w\-]", "_", first_tag).lower()

    project_dir = PROCESSED / safe_tag
    project_dir.mkdir(parents=True, exist_ok=True)

    # 生成文件名（使用标题关键词）
    summary = extracted.get("summary", source_id)[:40]
    safe_summary = _safe_filename_part(summary, default="untitled", max_len=40)
    safe_source_id = _safe_filename_part(source_id, default="unknown", max_len=80)
    filename = f"{safe_summary}__{safe_source_id[:12]}.md"
    target = project_dir / filename

    # 写入
    content = _format_markdown(frontmatter, body_data)
    success = _atomic_write(target, content)

    if success:
        logger.info("Written processed: %s", target)
        return {
            "success": True,
            "path": str(target),
            "id": frontmatter.get("id"),
            "error": None,
        }
    else:
        _move_to_quarantine(source_id, "write_error", content)
        return {
            "success": False,
            "path": None,
            "id": frontmatter.get("id"),
            "error": "write_error",
        }
