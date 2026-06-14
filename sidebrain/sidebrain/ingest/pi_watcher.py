"""Pi 会话观察器 — 增量扫描 Pi 会话目录并写入原始副本。

流程：
1. 读取游标（~/.sidebrain/state/pi_cursor.json）
2. 按 mtime 增量扫描 Pi sessions 目录
3. 解析 → 原子写入 ~/.sidebrain/knowledge/raw/pi/<session_id>.md
4. 推进游标
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from sidebrain.ingest.pi_parser import parse_all_sessions
from sidebrain.paths import PI_SESSIONS, RAW_PI, STATE

logger = logging.getLogger(__name__)

CURSOR_FILE = STATE / "pi_cursor.json"


def _load_cursor() -> dict:
    """加载游标文件。"""
    if CURSOR_FILE.exists():
        try:
            return json.loads(CURSOR_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load cursor, resetting: %s", e)
    return {}


def _save_cursor(cursor: dict) -> None:
    """原子写入游标文件。"""
    tmp = CURSOR_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cursor, indent=2))
    with tmp.open("rb") as f:
        os.fsync(f.fileno())
    tmp.replace(CURSOR_FILE)


def _atomic_write(target: Path, content: str) -> None:
    """原子写入文件（tmp → rename）。"""
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    # fsync
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    tmp.replace(target)


def _cleanup_tmp() -> None:
    """清理残留的 tmp 文件（崩溃恢复）。"""
    for f in RAW_PI.glob("*.md.tmp"):
        logger.info("Cleaning up stale temp file: %s", f)
        f.unlink(missing_ok=True)
    cursor_tmp = CURSOR_FILE.with_suffix(".json.tmp")
    if cursor_tmp.exists():
        logger.info("Cleaning up stale cursor temp: %s", cursor_tmp)
        cursor_tmp.unlink(missing_ok=True)


def ingest_pi_sessions(
    session_dir: str | Path | None = None,
    batch_size: int = 10,
    max_size_mb: int = 50,
) -> dict[str, Any]:
    """增量摄入 Pi 会话。

    Args:
        session_dir: Pi 会话目录路径。默认从 paths.py 读取。
        batch_size: 单次处理最大数量。
        max_size_mb: 单个文件最大 MB 数。

    Returns:
        摄入结果统计：
        {
            "ingested": int,      # 成功摄入数
            "skipped": int,       # 跳过数（过大/已存在）
            "errors": int,        # 失败数
            "new_cursor": dict,   # 新的游标值
        }
    """
    if session_dir is None:
        session_dir = PI_SESSIONS

    session_dir = Path(session_dir)
    if not session_dir.exists():
        logger.warning("Session directory not found: %s", session_dir)
        return {"ingested": 0, "skipped": 0, "errors": 0, "new_cursor": {}}

    # 崩溃恢复：清理残留 tmp
    _cleanup_tmp()

    # 加载游标
    cursor = _load_cursor()
    logger.debug("Loaded cursor: %s", cursor)

    # 解析会话
    all_parsed = parse_all_sessions(
        session_dir=session_dir,
        glob_pattern="**/*.jsonl",
        cursor=cursor,
    )

    if not all_parsed:
        logger.info("No new Pi sessions to ingest")
        return {"ingested": 0, "skipped": 0, "errors": 0, "new_cursor": cursor}

    logger.info("Found %d new sessions", len(all_parsed))

    # 按 mtime 排序
    all_parsed.sort(key=lambda x: x.get("_mtime_ns", 0))

    # 限制批次大小
    batch = all_parsed[:batch_size]

    ingested = 0
    skipped = 0
    errors = 0
    max_bytes = max_size_mb * 1024 * 1024

    for parsed in batch:
        session_id = parsed["session_id"]
        target_file = RAW_PI / f"{session_id}.md"

        # 文件大小检查
        file_path = parsed.get("_file_path", "")
        try:
            if file_path and Path(file_path).stat().st_size > max_bytes:
                logger.warning("Session too large, skipping: %s (>%dMB)", session_id, max_size_mb)
                # 写入 quarantine
                _write_quarantine(session_id, "too_large", str(parsed.get("_file_path", "")))
                skipped += 1
                continue
        except OSError:
            pass

        try:
            markdown = parsed["markdown"]
            _atomic_write(target_file, markdown)
            ingested += 1
        except Exception as e:
            logger.error("Failed to write session %s: %s", session_id, e)
            _write_quarantine(session_id, f"write_error:{e}", str(parsed.get("_file_path", "")))
            errors += 1

    # 推进游标
    if batch:
        last_mtime = max(p.get("_mtime_ns", 0) for p in batch)
        cursor["last_mtime_ns"] = last_mtime
        cursor["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _save_cursor(cursor)

    result = {
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors,
        "new_cursor": cursor,
    }
    logger.info("Ingest complete: %s", result)
    return result


def _write_quarantine(session_id: str, reason: str, file_path: str) -> None:
    """将失败的会话写入隔离区。"""
    from sidebrain.paths import QUARANTINE

    ts = time.strftime("%Y%m%d%H%M%S")
    q_file = QUARANTINE / f"pi__{session_id}__{ts}__{reason}.txt"
    try:
        q_file.write_text(
            f"Session ID: {session_id}\n"
            f"File: {file_path}\n"
            f"Reason: {reason}\n"
            f"Timestamp: {ts}\n"
            f"Status: quarantined\n"
        )
    except Exception as e:
        logger.error("Failed to write quarantine record: %s", e)
