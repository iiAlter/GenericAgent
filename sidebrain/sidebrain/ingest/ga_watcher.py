"""GA 对话观察器 — 增量扫描 GA model_responses 目录并写入原始副本。

流程：
1. 读取游标（~/.sidebrain/state/ga_cursor.json）
2. 按 mtime 增量扫描 GA temp/model_responses/
3. 转换 .txt → markdown
4. 原子写入 ~/.sidebrain/knowledge/raw/ga/<session_id>.md
5. 推进游标
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from sidebrain.paths import GA_ROOT, RAW_GA, STATE

logger = logging.getLogger(__name__)

CURSOR_FILE = STATE / "ga_cursor.json"
MODEL_RESPONSES_DIR = GA_ROOT / "temp" / "model_responses"


def _load_cursor() -> dict:
    if CURSOR_FILE.exists():
        try:
            return json.loads(CURSOR_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cursor(cursor: dict) -> None:
    tmp = CURSOR_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cursor, indent=2))
    with tmp.open("rb") as f:
        os.fsync(f.fileno())
    tmp.replace(CURSOR_FILE)


def _atomic_write(target: Path, content: str) -> None:
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    tmp.replace(target)


def _parse_model_response(filepath: Path) -> dict | None:
    """解析单个 model_responses 文件，提取对话内容。

    Returns:
        {
            "session_id": str,
            "turns": [{"role": "user"|"assistant", "content": str, "timestamp": str}],
            "raw_text": str,
        }
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to read %s: %s", filepath.name, e)
        return None

    # 提取文件名中的 session id
    session_id = filepath.stem

    turns: list[dict] = []
    # 按 === Prompt === / === Response === 分块
    blocks = re.split(r"\n=== (Prompt|Response) === (.+?)\n", text)

    i = 0
    while i < len(blocks):
        if blocks[i] in ("Prompt", "Response") and i + 2 < len(blocks):
            role = "user" if blocks[i] == "Prompt" else "assistant"
            timestamp = blocks[i + 1].strip()
            content = blocks[i + 2].strip()

            # 尝试解析内容：Response 块是 Python 字面量（单引号），Prompt 块是 JSON
            try:
                # 优先尝试 Python literal（GA Response 格式）
                parsed = ast.literal_eval(content)
            except (ValueError, SyntaxError):
                try:
                    parsed = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    parsed = None

            if isinstance(parsed, list):
                texts = []
                for item in parsed:
                    if isinstance(item, dict) and item.get("type") == "text":
                        t = item.get("text", "")
                        # 过滤 <think> 块
                        t = re.sub(r"<think>.*?</think>", "", t, flags=re.DOTALL)
                        texts.append(t.strip())
                content = "\n".join(t for t in texts if t)
            elif isinstance(parsed, dict) and parsed.get("role") == "user":
                user_content = parsed.get("content", [])
                if isinstance(user_content, list):
                    texts = []
                    for item in user_content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            texts.append(item.get("text", ""))
                    content = "\n".join(texts)

            turns.append({
                "role": role,
                "timestamp": timestamp,
                "content": content[:2000],  # 每轮最多 2000 字符
            })
            i += 3
        else:
            i += 1

    if not turns:
        return None

    return {
        "session_id": session_id,
        "turns": turns,
        "raw_text": text,
    }


def _format_markdown(parsed: dict) -> str:
    """将解析结果格式化为 markdown。"""
    lines = [f"# GA Session: {parsed['session_id']}", ""]

    for turn in parsed["turns"]:
        role_label = "👤 User" if turn["role"] == "user" else "🤖 GA"
        ts = turn["timestamp"]
        content = turn["content"]

        lines.append(f"## {role_label} — {ts}")
        lines.append("")
        lines.append(content)
        lines.append("")

    return "\n".join(lines)


def ingest_ga_sessions(
    batch_size: int = 10,
    max_size_mb: int = 10,
) -> dict[str, Any]:
    """增量摄入 GA model_responses。

    Args:
        batch_size: 单次处理最大数量。
        max_size_mb: 单个源文件最大 MB 数。

    Returns:
        {"ingested": int, "skipped": int, "errors": int, "new_cursor": dict}
    """
    if not MODEL_RESPONSES_DIR.exists():
        logger.warning("model_responses directory not found: %s", MODEL_RESPONSES_DIR)
        return {"ingested": 0, "skipped": 0, "errors": 0, "new_cursor": {}}

    RAW_GA.mkdir(parents=True, exist_ok=True)

    cursor = _load_cursor()
    logger.debug("Loaded GA cursor: %s", cursor)

    max_bytes = max_size_mb * 1024 * 1024

    # 收集新文件（按 mtime 增量）
    new_files: list[Path] = []
    for f in sorted(MODEL_RESPONSES_DIR.glob("model_responses_*.txt")):
        try:
            mtime = f.stat().st_mtime
            prev_mtime = cursor.get(f.name, 0)
            if mtime > prev_mtime:
                new_files.append(f)
        except OSError:
            continue

    if not new_files:
        logger.info("No new GA sessions to ingest")
        return {"ingested": 0, "skipped": 0, "errors": 0, "new_cursor": cursor}

    logger.info("Found %d new GA sessions", len(new_files))

    batch = new_files[:batch_size]
    ingested = 0
    skipped = 0
    errors = 0

    for f in batch:
        try:
            file_size = f.stat().st_size
            if file_size > max_bytes:
                logger.warning("GA session too large, skipping: %s (%dMB)", f.name, file_size // (1024 * 1024))
                skipped += 1
                cursor[f.name] = f.stat().st_mtime
                continue

            parsed = _parse_model_response(f)
            if not parsed:
                logger.warning("Failed to parse GA session: %s", f.name)
                errors += 1
                cursor[f.name] = f.stat().st_mtime
                continue

            markdown = _format_markdown(parsed)
            target = RAW_GA / f"{parsed['session_id']}.md"
            _atomic_write(target, markdown)

            cursor[f.name] = f.stat().st_mtime
            ingested += 1
        except Exception as e:
            logger.error("Failed to ingest GA session %s: %s", f.name, e)
            errors += 1
            try:
                cursor[f.name] = f.stat().st_mtime
            except Exception:
                pass

    _save_cursor(cursor)

    result = {
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors,
        "new_cursor": cursor,
    }
    logger.info("GA ingest complete: %s", result)
    return result
