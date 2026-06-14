"""Pi 镜像同步 — 将 processed/ 内容同步到 Pi 记忆库。

过滤规则：
- 只同步 tags 含 general / rule / preference 的条目
- rule 类 → ~/.pi/agent/rules/sidebrain/
- 其他 → ~/.pi/agent/memories/sidebrain/

幂等性：
- target mtime 较新 → 跳过
- 内容一致 → 跳过
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from sidebrain.paths import PI_MEMORIES_MIRROR, PI_RULES_MIRROR, PROCESSED

logger = logging.getLogger(__name__)


def _extract_frontmatter(text: str) -> dict | None:
    """提取 JSON frontmatter。"""
    import re
    match = re.search(r"^---\s*(\{.*?\})\s*---\s*", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def _make_pi_memory_markdown(
    frontmatter: dict,
    body_summary: str,
    body_key_points: list[str],
) -> str:
    """格式化为 Pi 记忆库兼容的 Markdown。

    Pi 记忆库 frontmatter 格式（根据 plan 所知）：
    ---
    created: YYYY-MM-DD
    tags: [tag1, tag2]
    ---
    """
    tags = frontmatter.get("tags", [])
    created = frontmatter.get("created", time.strftime("%Y-%m-%d"))

    lines = [
        "---",
        f"created: {created}",
        f"tags: {json.dumps(tags, ensure_ascii=False)}",
        "---",
        "",
    ]

    # 标题
    lines.append(f"# {body_summary[:80]}")
    lines.append("")

    # 关键点
    for kp in body_key_points:
        lines.append(f"- {kp}")

    lines.append("")
    lines.append(f"*来源: {frontmatter.get('source', '?')} / {frontmatter.get('source_id', '?')[:16]}*")

    return "\n".join(lines)


def _is_rule_entry(frontmatter: dict) -> bool:
    """判断是否规则类条目。"""
    tags = frontmatter.get("tags", [])
    # rule 标签 → 规则
    if "rule" in tags:
        return True
    # 标题含 rule/preference
    return False


def _compute_content_hash(text: str) -> str:
    """计算内容哈希用于幂等性检查。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sync_to_pi(
    tags_filter: list[str] | None = None,
    max_items: int = 50,
    dry_run: bool = False,
) -> dict[str, Any]:
    """同步 processed/ 内容到 Pi 记忆库。

    Args:
        tags_filter: 只同步包含这些标签的条目。
        max_items: 最大同步条数。
        dry_run: 仅模拟。

    Returns:
        同步统计。
    """
    if tags_filter is None:
        tags_filter = ["general", "rule", "preference"]

    PI_MEMORIES_MIRROR.mkdir(parents=True, exist_ok=True)
    PI_RULES_MIRROR.mkdir(parents=True, exist_ok=True)

    if not PROCESSED.exists():
        logger.warning("Processed dir not found")
        return {"total": 0, "synced": 0, "skipped": 0, "errors": 0}

    files = sorted(PROCESSED.rglob("*.md"))
    logger.info("Found %d processed entries", len(files))

    total = 0
    synced = 0
    skipped = 0
    errors = 0

    for f in files[:max_items]:
        total += 1
        try:
            text = f.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("Failed to read %s: %s", f, e)
            errors += 1
            continue

        frontmatter = _extract_frontmatter(text)
        if not frontmatter:
            logger.debug("No frontmatter in %s, skipping", f.name)
            skipped += 1
            continue

        # 标签过滤 + 置信度兜底
        tags = frontmatter.get("tags", [])
        confidence = frontmatter.get("confidence", 0)
        tags_match = any(t in tags_filter for t in tags)
        # 高置信度(>=0.7)的内容也同步，不限于特定标签
        if not tags_match and confidence < 0.7:
            logger.debug("Skipping: tags %s no match, confidence %.2f < 0.7", tags, confidence)
            skipped += 1
            continue

        # 提取摘要和关键点
        body_text = text.split("---", 2)[-1] if text.count("---") >= 2 else text
        try:
            import re as _re
            body_json_match = _re.search(r"```json\n(.*?)\n```", body_text, _re.DOTALL)
            if body_json_match:
                body_data = json.loads(body_json_match.group(1))
            else:
                body_data = {}
        except (json.JSONDecodeError, AttributeError):
            body_data = {}

        body_summary = body_data.get("summary", f.name)
        body_key_points = body_data.get("key_points", [])

        # 生成 Pi 格式 Markdown
        pi_markdown = _make_pi_memory_markdown(frontmatter, body_summary, body_key_points)

        # 确定目标目录
        is_rule = _is_rule_entry(frontmatter)
        target_dir = PI_RULES_MIRROR if is_rule else PI_MEMORIES_MIRROR

        # 生成文件名
        safe_name = body_summary[:50]
        safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in safe_name).strip()
        filename = f"{safe_name}__{frontmatter.get('source_id', frontmatter.get('id', 'unknown'))[:16]}.md"
        target = target_dir / filename

        # 幂等性检查
        if target.exists():
            existing_text = target.read_text(encoding="utf-8")
            if _compute_content_hash(existing_text) == _compute_content_hash(pi_markdown):
                logger.debug("Content unchanged, skipping: %s", filename)
                skipped += 1
                continue
            # target mtime 较新 → 跳过（用户手动修改过）
            if target.stat().st_mtime_ns > f.stat().st_mtime_ns:
                logger.debug("Target newer than source, skipping: %s", filename)
                skipped += 1
                continue

        if dry_run:
            logger.info("[DRY RUN] Would sync to: %s", target)
            synced += 1
            continue

        # 原子写入
        try:
            tmp = target.with_suffix(".md.tmp")
            tmp.write_text(pi_markdown, encoding="utf-8")
            with tmp.open("rb") as f_tmp:
                os.fsync(f_tmp.fileno())
            tmp.replace(target)
            synced += 1
            logger.info("Synced to Pi: %s", target)
        except Exception as e:
            logger.error("Failed to write %s: %s", target, e)
            errors += 1

    result = {
        "total": total,
        "synced": synced,
        "skipped": skipped,
        "errors": errors,
    }
    logger.info("Sync complete: %s", result)
    return result
