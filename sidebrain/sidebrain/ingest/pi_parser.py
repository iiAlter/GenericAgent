"""Pi 会话解析器 — 将 JSONL v3 格式解析为标准化 Markdown。

Pi 会话格式：
- JSONL v3，每行一个 JSON 对象
- 首行: {"type":"session","version":3,"id":"...","timestamp":"...","cwd":"..."}
- 中间行: 消息类型（message/model_change/thinking_level_change）
- 消息 role: user/assistant/toolResult
- content 块类型: text/thinking/toolCall/toolResult
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def parse_session_file(path: str | Path) -> dict[str, Any] | None:
    """解析单个 Pi JSONL 会话文件。

    Args:
        path: JSONL 文件路径。

    Returns:
        包含解析结果的字典，或 None（如果解析失败）。
        返回结构:
        {
            "session_id": str,
            "cwd": str | None,
            "timestamp": str | None,
            "model": str | None,
            "provider": str | None,
            "messages": list[dict],
            "content_hash": str,
            "markdown": str,       # 标准化 Markdown 输出
        }
    """
    path = Path(path)
    if not path.exists():
        logger.warning("Session file not found: %s", path)
        return None

    try:
        raw_bytes = path.read_bytes()
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        text = raw_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        logger.error("Failed to read %s: %s", path, e)
        return None

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        logger.warning("Empty session file: %s", path)
        return None

    # 解析 session 首行
    try:
        first = json.loads(lines[0])
    except json.JSONDecodeError:
        logger.warning("Invalid JSON on first line: %s", path)
        return None

    session_id = first.get("id", path.stem)
    session_version = first.get("version", "?")
    cwd = first.get("cwd")
    session_ts = first.get("timestamp")

    model = None
    provider = None
    messages: list[dict[str, Any]] = []

    for i, line in enumerate(lines[1:], start=2):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("Line %d: invalid JSON, skipping", i)
            continue

        obj_type = obj.get("type", "")

        if obj_type == "model_change":
            provider = obj.get("provider", provider)
            model = obj.get("modelId", model)
            continue

        if obj_type == "thinking_level_change":
            continue

        if obj_type != "message":
            continue

        msg = obj.get("message", {})
        role = msg.get("role", "")
        content_blocks = msg.get("content", [])
        ts = obj.get("timestamp", "")

        if isinstance(content_blocks, str):
            # 某些老版本可能用字符串
            content_blocks = [{"type": "text", "text": content_blocks}]

        if not isinstance(content_blocks, list):
            content_blocks = []

        parsed_blocks = [_parse_content_block(b) for b in content_blocks]
        parsed_blocks = [b for b in parsed_blocks if b is not None]

        msg_entry: dict[str, Any] = {
            "role": role,
            "timestamp": ts,
            "blocks": parsed_blocks,
        }

        if role == "toolResult":
            msg_entry["tool_call_id"] = msg.get("toolCallId", "")
            msg_entry["tool_name"] = msg.get("toolName", "")

        messages.append(msg_entry)

    markdown = _format_as_markdown(
        session_id=session_id,
        cwd=cwd,
        session_ts=session_ts,
        model=model,
        provider=provider,
        messages=messages,
    )

    return {
        "session_id": session_id,
        "cwd": cwd,
        "timestamp": session_ts,
        "model": model,
        "provider": provider,
        "messages": messages,
        "content_hash": content_hash,
        "markdown": markdown,
    }


def _parse_content_block(block: dict) -> dict | None:
    """解析单个 content block。"""
    if not isinstance(block, dict):
        return None

    btype = block.get("type", "")

    if btype == "text":
        return {"type": "text", "text": block.get("text", "")}

    if btype == "thinking":
        # thinking 内容太长，只保留前 200 字符作为概要
        thinking_text = block.get("thinking", "")
        if len(thinking_text) > 200:
            thinking_text = thinking_text[:200] + "\n  ...(truncated)"
        return {"type": "thinking", "text": thinking_text}

    if btype == "toolCall" or btype == "tool_use":
        args = block.get("arguments") or block.get("input") or {}
        return {
            "type": "toolCall",
            "name": block.get("name", ""),
            "arguments": json.dumps(args, ensure_ascii=False),
        }

    if btype == "toolResult" or btype == "tool_result":
        content = block.get("content", "")
        if isinstance(content, list):
            texts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    texts.append(c.get("text", ""))
            content = "\n".join(texts)
        # 截断长内容
        content_str = str(content)
        if len(content_str) > 500:
            content_str = content_str[:500] + "\n  ...(truncated)"
        return {
            "type": "toolResult",
            "tool_name": block.get("name", ""),
            "text": content_str,
        }

    return None


def _format_as_markdown(
    session_id: str,
    cwd: str | None,
    session_ts: str | None,
    model: str | None,
    provider: str | None,
    messages: list[dict],
) -> str:
    """将解析结果格式化为标准化 Markdown。"""

    # 提取日期字符串
    date_str = _extract_date(session_ts)

    lines: list[str] = [
        f"# Pi Session: {session_id}",
        "",
    ]
    if date_str:
        lines.append(f"**Date:** {date_str}")
    if cwd:
        lines.append(f"**CWD:** `{cwd}`")
    if model:
        lines.append(f"**Model:** {model} ({provider or '?'})")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in messages:
        role = msg["role"]
        ts = msg.get("timestamp", "")

        # 时间戳
        if ts:
            # 只保留可读部分
            ts_short = ts.replace("T", " ").split(".")[0]
            header = f"### {role} ({ts_short})"
        else:
            header = f"### {role}"

        lines.append(header)
        lines.append("")

        if role == "toolResult":
            tool_name = msg.get("tool_name", "")
            if tool_name:
                lines.append(f"**Tool:** `{tool_name}`")

        for block in msg["blocks"]:
            btype = block["type"]
            if btype == "text":
                text = block["text"]
                # 检测是否是代码块
                if text.strip().startswith("```"):
                    lines.append(text)
                else:
                    lines.append(text)
                lines.append("")
            elif btype == "thinking":
                lines.append(f"> 💭 {block['text']}")
                lines.append("")
            elif btype == "toolCall":
                lines.append(f"- 🛠 **{block['name']}**")
                args_text = block["arguments"]
                if len(args_text) < 200:
                    lines.append(f"  `{args_text}`")
                else:
                    lines.append(f"  ```json\n{args_text}\n  ```")
                lines.append("")
            elif btype == "toolResult":
                lines.append(f"```\n{block['text']}\n```")
                lines.append("")

        lines.append("---")
        lines.append("")

    markdown = "\n".join(lines).strip()
    return markdown


def _extract_date(ts_str: str | None) -> str | None:
    """从 ISO 时间戳中提取日期。"""
    if not ts_str:
        return None
    try:
        if "T" in ts_str:
            return ts_str.split("T")[0]
    except Exception:
        pass
    return ts_str[:10] if ts_str else None


def parse_all_sessions(
    session_dir: str | Path,
    glob_pattern: str = "**/*.jsonl",
    cursor: dict | None = None,
) -> list[dict]:
    """批量解析 Pi 会话文件，支持增量。

    Args:
        session_dir: 会话目录路径。
        glob_pattern: 文件匹配模式。
        cursor: 游标字典，包含上次扫描的 mtime_ns。

    Returns:
        解析结果列表。
    """
    session_dir = Path(session_dir)
    if not session_dir.exists():
        logger.warning("Session dir not found: %s", session_dir)
        return []

    last_mtime_ns = (cursor or {}).get("last_mtime_ns", 0)
    results: list[dict] = []

    for f in sorted(session_dir.glob(glob_pattern)):
        try:
            mtime_ns = f.stat().st_mtime_ns
        except OSError:
            continue

        if mtime_ns <= last_mtime_ns:
            continue

        parsed = parse_session_file(f)
        if parsed:
            parsed["_file_path"] = str(f)
            parsed["_mtime_ns"] = mtime_ns
            results.append(parsed)

    return results
