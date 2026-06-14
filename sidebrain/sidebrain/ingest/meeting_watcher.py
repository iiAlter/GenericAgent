"""会议纪要观察器 — 从 sources/meetings/ 增量摄入会议纪要。

从 .md / .txt 文件中提取：
- H1/H2 标题
- 行动项（- [ ] / - [x]）
- 参会人
- 日期
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from sidebrain.paths import RAW_MEETINGS, SOURCES_MEETINGS, STATE

logger = logging.getLogger(__name__)

CURSOR_FILE = STATE / "meeting_cursor.json"

# 行动项正则
ACTION_ITEM_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.+)", re.MULTILINE)
# H1 / H2 标题
HEADING_RE = re.compile(r"^#{1,2}\s+(.+)", re.MULTILINE)
# 参会人（常见模式）
ATTENDEE_RE = re.compile(
    r"(?:参会[人者]|出席|Participants?|Attendees?)[：:]\s*(.*)",
    re.IGNORECASE,
)
# 日期（常见格式）
DATE_RE = re.compile(
    r"(?:日期|时间?|Date|Time)[：:]\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})",
    re.IGNORECASE,
)


def _load_cursor() -> dict:
    """加载游标文件。"""
    if CURSOR_FILE.exists():
        try:
            return json.loads(CURSOR_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load meeting cursor, resetting: %s", e)
    return {}


def _save_cursor(cursor: dict) -> None:
    """原子写入游标文件。"""
    tmp = CURSOR_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cursor, indent=2))
    with tmp.open("rb") as f:
        os.fsync(f.fileno())
    tmp.replace(CURSOR_FILE)


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


def parse_meeting_file(path: str | Path) -> dict[str, Any] | None:
    """解析单个会议纪要文件。

    Args:
        path: .md 或 .txt 文件路径。

    Returns:
        解析结果字典，或 None。
    """
    path = Path(path)
    if not path.exists():
        return None

    try:
        raw_bytes = path.read_bytes()
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        text = raw_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        logger.error("Failed to read %s: %s", path, e)
        return None

    # 提取元数据
    headings = HEADING_RE.findall(text)
    action_items_raw = ACTION_ITEM_RE.findall(text)

    action_items = []
    for checked, item_text in action_items_raw:
        action_items.append({
            "checked": checked.lower() == "x",
            "text": item_text.strip(),
        })

    attendees = _extract_attendees(text)
    date = _extract_date(text)
    title = headings[0] if headings else path.stem

    # 格式化 markdown
    markdown = _format_meeting_markdown(
        title=title,
        date=date,
        attendees=attendees,
        headings=headings,
        action_items=action_items,
        original_text=text,
        source_path=str(path.relative_to(SOURCES_MEETINGS.parent) if SOURCES_MEETINGS in path.parents else path),
    )

    return {
        "source_id": path.stem,
        "title": title,
        "date": date,
        "attendees": attendees,
        "headings": headings,
        "action_items": action_items,
        "content_hash": content_hash,
        "markdown": markdown,
    }


def _extract_attendees(text: str) -> list[str]:
    """提取参会人列表。"""
    for m in ATTENDEE_RE.finditer(text):
        raw = m.group(1).strip()
        # 尝试分割（逗号/空格/、/、/ 分隔）
        parts = re.split(r"[,，、/\s]+", raw)
        return [p.strip() for p in parts if p.strip()]
    return []


def _extract_date(text: str) -> str | None:
    """提取日期。"""
    for m in DATE_RE.finditer(text):
        return m.group(1).replace("/", "-")
    return None


def _format_meeting_markdown(
    title: str,
    date: str | None,
    attendees: list[str],
    headings: list[str],
    action_items: list[dict],
    original_text: str,
    source_path: str,
) -> str:
    """格式化为标准化 Markdown。"""
    lines = [
        f"# 会议纪要: {title}",
        "",
    ]
    if date:
        lines.append(f"**日期:** {date}")
    if attendees:
        lines.append(f"**参会人:** {', '.join(attendees)}")
    lines.append(f"**来源:** {source_path}")
    lines.append("")
    lines.append("---")
    lines.append("")

    if action_items:
        lines.append("## 行动项")
        lines.append("")
        for item in action_items:
            checkbox = "x" if item["checked"] else " "
            lines.append(f"- [{checkbox}] {item['text']}")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## 原始内容")
    lines.append("")
    lines.append(original_text)

    return "\n".join(lines)


def _find_meeting_files(source_dir: Path) -> list[Path]:
    """查找所有会议纪要文件（.md / .txt），递归子目录。

    Python 3.11 的 Path.glob 不支持花括号扩展，所以分别查找。
    """
    files: set[Path] = set()
    for f in source_dir.rglob("*.md"):
        if f.name.lower() != "readme.md":
            files.add(f)
    for f in source_dir.rglob("*.txt"):
        files.add(f)
    return sorted(files, key=lambda p: p.stat().st_mtime_ns if p.exists() else 0)


def scan_meetings(
    source_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """扫描会议纪要目录，支持增量。

    Args:
        source_dir: 来源目录，默认 paths.SOURCES_MEETINGS。

    Returns:
        解析结果列表。
    """
    if source_dir is None:
        source_dir = SOURCES_MEETINGS

    source_dir = Path(source_dir)
    if not source_dir.exists():
        logger.warning("Meeting source dir not found: %s", source_dir)
        return []

    cursor = _load_cursor()
    last_mtime_ns = cursor.get("last_mtime_ns", 0)

    results: list[dict] = []

    for f in _find_meeting_files(source_dir):
        # 跳过 .gitkeep
        if f.name == ".gitkeep":
            continue

        try:
            mtime_ns = f.stat().st_mtime_ns
        except OSError:
            continue

        if mtime_ns <= last_mtime_ns:
            continue

        parsed = parse_meeting_file(f)
        if parsed:
            parsed["_file_path"] = str(f)
            parsed["_mtime_ns"] = mtime_ns
            results.append(parsed)

    return results


def ingest_meetings(
    source_dir: str | Path | None = None,
    batch_size: int = 10,
) -> dict[str, Any]:
    """增量摄入会议纪要。

    Args:
        source_dir: 来源目录。
        batch_size: 单次最大处理数。

    Returns:
        摄入统计。
    """
    if source_dir is None:
        source_dir = SOURCES_MEETINGS

    source_dir = Path(source_dir)
    RAW_MEETINGS.mkdir(parents=True, exist_ok=True)

    # 清理残留 tmp
    for f in RAW_MEETINGS.glob("*.md.tmp"):
        f.unlink(missing_ok=True)

    meetings = scan_meetings(source_dir=source_dir)
    if not meetings:
        logger.info("No new meetings to ingest")
        return {"ingested": 0, "skipped": 0, "errors": 0}

    logger.info("Found %d new meetings", len(meetings))
    meetings.sort(key=lambda x: x.get("_mtime_ns", 0))
    batch = meetings[:batch_size]

    ingested = 0
    errors = 0

    for meeting in batch:
        source_id = meeting["source_id"]
        # 用日期+标题作为文件名
        date_part = meeting.get("date", "") or "unknown"
        safe_title = re.sub(r"[^\w\- ]", "", meeting["title"])[:40]
        filename = f"{date_part}__{safe_title}__{source_id}.md"
        target_file = RAW_MEETINGS / filename

        try:
            _atomic_write(target_file, meeting["markdown"])
            ingested += 1
        except Exception as e:
            logger.error("Failed to write meeting %s: %s", source_id, e)
            errors += 1

    # 推进游标
    if batch:
        last_mtime = max(m.get("_mtime_ns", 0) for m in batch)
        cursor = _load_cursor()
        cursor["last_mtime_ns"] = last_mtime
        cursor["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _save_cursor(cursor)

    result = {
        "ingested": ingested,
        "skipped": 0,
        "errors": errors,
    }
    logger.info("Meeting ingest complete: %s", result)
    return result
