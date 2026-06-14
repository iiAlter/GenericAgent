"""去重引擎 — 基于内容哈希和 Jaccard 相似度去重。

策略：
1. 精确去重：content_hash 完全匹配 → 跳过
2. 模糊去重：标题/标签 Jaccard ≥ 0.7 → 合并
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from sidebrain.paths import PROCESSED

logger = logging.getLogger(__name__)


def _load_existing() -> dict[str, dict[str, Any]]:
    """加载所有已存在的 processed 条目。

    Returns:
        {content_hash: metadata} 字典。
    """
    existing: dict[str, dict[str, Any]] = {}

    if not PROCESSED.exists():
        return existing

    for f in PROCESSED.rglob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
            # 提取 frontmatter（--- 之间的 JSON 或 YAML）
            frontmatter = _extract_frontmatter(text)
            if frontmatter:
                content_hash = frontmatter.get("content_hash", "")
                if content_hash:
                    existing[content_hash] = {
                        "path": str(f),
                        "frontmatter": frontmatter,
                    }
        except Exception as e:
            logger.debug("Failed to read existing %s: %s", f, e)

    return existing


def _extract_frontmatter(text: str) -> dict[str, Any] | None:
    """从 Markdown 文本中提取 frontmatter。

    支持 JSON frontmatter（---{...}---）格式。
    """
    import re

    # JSON frontmatter: ---{json}---
    match = re.search(r"^---\s*(\{.*?\})\s*---\s*", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def _jaccard_similarity(a: set, b: set) -> float:
    """计算两个集合的 Jaccard 相似度。"""
    if not a or not b:
        return 0.0
    intersection = a & b
    union = a | b
    return len(intersection) / len(union)


def check_duplicate(
    content_hash: str,
    tags: list[str] | None = None,
    title: str = "",
) -> dict[str, Any] | None:
    """检查条目是否重复。

    Args:
        content_hash: 内容 SHA-256 哈希。
        tags: 可选的标签列表。
        title: 可选的标题。

    Returns:
        如果重复，返回已存在的条目信息；否则返回 None。
    """
    existing = _load_existing()

    # 1. 精确去重
    if content_hash in existing:
        logger.debug("Exact duplicate found: %s", content_hash[:16])
        return existing[content_hash]

    # 2. 模糊去重（基于标签 + 标题词）
    if tags or title:
        new_tags = set(tags or [])
        new_words = set(re.findall(r"\w+", title.lower())) if title else set()

        for e_hash, e_info in existing.items():
            e_fm = e_info.get("frontmatter", {})
            e_tags = set(e_fm.get("tags", []))
            e_title = e_fm.get("title", "")

            # 标签 Jaccard
            tag_sim = _jaccard_similarity(new_tags, e_tags) if new_tags or e_tags else 0.0

            # 标题词 Jaccard
            e_words = set(re.findall(r"\w+", e_title.lower()))
            word_sim = _jaccard_similarity(new_words, e_words) if new_words or e_words else 0.0

            if tag_sim >= 0.7 or word_sim >= 0.7:
                logger.debug(
                    "Fuzzy duplicate: hash=%s tag_sim=%.2f word_sim=%.2f",
                    content_hash[:16],
                    tag_sim,
                    word_sim,
                )
                return e_info

    return None


def count_processed() -> int:
    """统计 processed 目录中的条目数。"""
    if not PROCESSED.exists():
        return 0
    return len(list(PROCESSED.rglob("*.md")))


def list_projects() -> list[str]:
    """列出 processed 中的所有项目目录。"""
    if not PROCESSED.exists():
        return []
    return sorted(
        p.name for p in PROCESSED.iterdir() if p.is_dir() and not p.name.startswith(".")
    )
