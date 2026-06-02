"""临时文本摄入 — 从 stdin 或参数接收文本，立即处理并存储。

用于快速记录用户的想法、笔记、临时知识。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from sidebrain.paths import RAW_AD_HOC, STATE

logger = logging.getLogger(__name__)

CURSOR_FILE = STATE / "ad_hoc_cursor.json"


def _atomic_write(target: Path, content: str) -> None:
    """原子写入文件。"""
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    tmp.replace(target)


def _cleanup_tmp() -> None:
    """清理残留 tmp。"""
    for f in RAW_AD_HOC.glob("*.md.tmp"):
        f.unlink(missing_ok=True)


def ingest_text(text: str, source: str = "manual") -> dict[str, Any]:
    """摄入单条临时文本。

    Args:
        text: 要摄入的文本内容。
        source: 来源标识（manual / piped / pasted）。

    Returns:
        摄入结果。
    """
    RAW_AD_HOC.mkdir(parents=True, exist_ok=True)
    _cleanup_tmp()

    text = text.strip()
    if not text:
        logger.warning("Empty text, skipping")
        return {"ingested": 0, "id": None}

    entry_id = str(uuid.uuid4())
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    date_str = time.strftime("%Y-%m-%d")

    # 生成简短的第一行作为标题
    first_line = text.split("\n")[0].strip()
    title = first_line[:60] if first_line else "untitled"

    markdown = (
        f"# {title}\n\n"
        f"**Date:** {date_str}\n"
        f"**Source:** {source}\n"
        f"**ID:** {entry_id}\n"
        f"**Hash:** {content_hash[:16]}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"{text}\n"
    )

    filename = f"{date_str}__{entry_id[:8]}.md"
    target = RAW_AD_HOC / filename

    try:
        _atomic_write(target, markdown)
        logger.info("Ingested ad-hoc text: %s", filename)
        return {"ingested": 1, "id": entry_id, "file": str(target)}
    except Exception as e:
        logger.error("Failed to write ad-hoc text: %s", e)
        return {"ingested": 0, "id": None, "error": str(e)}


def ingest_stdin() -> dict[str, Any]:
    """从 stdin 读取文本并摄入。"""
    text = sys.stdin.read()
    return ingest_text(text, source="stdin")
