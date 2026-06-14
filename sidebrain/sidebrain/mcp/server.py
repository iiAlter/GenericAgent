"""MCP Server — 通过 stdio JSON-RPC 2.0 暴露 sidebrain 功能。

工具列表：
- sidebrain_search(query, limit=10) — 搜索处理后的记忆
- sidebrain_ingest(text, source="ad-hoc") — 立即摄入文本
- sidebrain_list_projects() — 列出项目列表
- sidebrain_get(topic_id) — 获取单条详情
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from sidebrain.ingest.ad_hoc import ingest_text
from sidebrain.paths import PROCESSED
from sidebrain.process.dedup import list_projects

logger = logging.getLogger(__name__)

# MCP JSON-RPC 协议常量
JSONRPC_VERSION = "2.0"


# ============================================================
# 工具实现
# ============================================================


def tool_sidebrain_search(
    query: str,
    limit: int = 10,
    status: str = "active",
    type: str = "",
    topic_key: str = "",
) -> dict[str, Any]:
    """搜索处理后的记忆条目。

    Args:
        query: 搜索关键词。
        limit: 最大返回条数，默认 10。

    Returns:
        {"entries": [...]}
    """
    if not PROCESSED.exists():
        return {"entries": []}

    results: list[dict[str, Any]] = []

    for entry, text in _load_entries_with_text():
        if status != "all" and entry.get("status", "active") != status:
            continue
        if type and entry.get("type", "note") != type:
            continue
        if topic_key and entry.get("topic_key", "") != topic_key:
            continue
        score = _score_entry(entry, text, query)
        if score <= 0:
            continue
        ranked = dict(entry)
        ranked["score"] = score
        results.append(ranked)

    results.sort(key=lambda e: (e.get("score", 0), e.get("updated", ""), e.get("created", "")), reverse=True)
    return {"entries": results[:limit]}


def tool_sidebrain_ingest(
    text: str = "",
    source: str = "ad-hoc",
    summary: str = "",
    key_points: list[str] | None = None,
    action_items: list[str] | None = None,
    decisions: list[Any] | None = None,
    tags: list[str] | None = None,
    topic_key: str = "",
    type: str = "note",
    status: str = "active",
    confidence: float | None = None,
) -> dict[str, Any]:
    """摄入文本或结构化记忆。

    传 summary 时直接写 processed；只传 text 时写 raw 等待处理。
    """
    if summary:
        from sidebrain.process.writer import write_processed

        result = write_processed(
            {
                "summary": summary,
                "key_points": key_points or [],
                "action_items": action_items or [],
                "decisions": decisions or [],
                "tags": tags or [],
                "topic_key": topic_key,
                "type": type,
                "status": status,
                "confidence": confidence,
            },
            source=source,
            source_path=f"mcp:{source}",
            source_id=topic_key or summary[:80],
        )
        return {
            "ingested": 1 if result.get("success") else 0,
            "id": result.get("id"),
            "path": result.get("path"),
            "updated": result.get("updated", False),
            "duplicate": result.get("duplicate", False),
            "error": result.get("error"),
        }

    result = ingest_text(text, source=source)
    return {"ingested": result["ingested"], "id": result.get("id")}


def tool_sidebrain_list_projects() -> dict[str, Any]:
    """列出所有项目目录。"""
    projects = list_projects()
    return {"projects": projects}


def tool_sidebrain_get(topic_id: str) -> dict[str, Any]:
    """按 ID 获取单条记忆详情。

    Args:
        topic_id: 记忆的 ID（frontmatter.id 或文件名关键词）。

    Returns:
        条目详情，未找到返回空。
    """
    if not PROCESSED.exists():
        return {"entry": None}

    entries = [entry for entry, _text in _load_entries_with_text()]
    for entry in entries:
        if topic_id == entry.get("id") or topic_id == entry.get("topic_key") or topic_id in Path(entry["file"]).stem:
            superseded_by_entries = _resolve_related(entries, [entry.get("superseded_by", "")])
            expanded = dict(entry)
            expanded["related_entries"] = _resolve_related(entries, entry.get("related", []))
            expanded["supersedes_entries"] = _resolve_related(entries, entry.get("supersedes", []))
            expanded["superseded_by_entry"] = superseded_by_entries[0] if superseded_by_entries else None
            return {"entry": expanded}

    return {"entry": None}


def tool_sidebrain_update(topic_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """更新单条记忆 frontmatter 字段。"""
    allowed = {
        "topic_key",
        "type",
        "status",
        "confidence",
        "tags",
        "project",
        "related",
        "supersedes",
        "superseded_by",
        "resolved_reason",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    return _update_entry_frontmatter(topic_id, updates)


def tool_sidebrain_resolve(topic_id: str, reason: str = "") -> dict[str, Any]:
    """将记忆标记为 resolved，默认搜索不再返回。"""
    updates: dict[str, Any] = {"status": "resolved"}
    if reason:
        updates["resolved_reason"] = reason
    return _update_entry_frontmatter(topic_id, updates)


def tool_sidebrain_supersede(topic_id: str, superseded_by: str = "", reason: str = "") -> dict[str, Any]:
    """将记忆标记为 superseded。"""
    updates: dict[str, Any] = {"status": "superseded"}
    if superseded_by:
        updates["superseded_by"] = superseded_by
    if reason:
        updates["resolved_reason"] = reason
    return _update_entry_frontmatter(topic_id, updates)


# ============================================================
# Helper
# ============================================================


def _extract_frontmatter(text: str) -> dict | None:
    """提取 JSON frontmatter 或 Node 端写入的 YAML-ish frontmatter。"""
    match = re.search(r"^---\s*(\{.*?\})\s*---\s*", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    match = re.search(r"^---\n(.*?)\n---\s*", text, re.DOTALL)
    if not match:
        return None

    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None

    if not isinstance(frontmatter, dict):
        return None

    text_hash = __import__("hashlib").sha256(text.encode("utf-8")).hexdigest()
    frontmatter.setdefault("schema_version", 1)
    frontmatter.setdefault("id", text_hash[:16])
    frontmatter.setdefault("source", "sidebrain")
    frontmatter.setdefault("source_id", frontmatter["id"])
    frontmatter.setdefault("content_hash", text_hash)
    frontmatter.setdefault("project", "")
    frontmatter.setdefault("confidence", 0)
    frontmatter.setdefault("type", "note")
    frontmatter.setdefault("status", "active")
    if not isinstance(frontmatter.get("tags", []), list):
        frontmatter["tags"] = [str(frontmatter.get("tags"))]
    return frontmatter


def _extract_body(text: str) -> dict:
    """提取 body JSON；兼容 Node 端的 H1 + bullet Markdown。"""
    match = re.search(r"```json\n(.*?)\n```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    body = {
        "summary": "",
        "key_points": [],
        "action_items": [],
        "decisions": [],
        "people_mentioned": [],
    }
    for line in text.splitlines():
        if line.startswith("# ") and not body["summary"]:
            body["summary"] = line[2:].strip()
        elif line.startswith("- "):
            body["key_points"].append(line[2:].strip())
    return body


def _load_entries_with_text() -> list[tuple[dict[str, Any], str]]:
    entries: list[tuple[dict[str, Any], str]] = []
    if not PROCESSED.exists():
        return entries

    for f in sorted(PROCESSED.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter = _extract_frontmatter(text)
        if not frontmatter:
            continue
        body_data = _extract_body(text)
        entries.append((
            {
                "id": frontmatter.get("id", ""),
                "topic_key": frontmatter.get("topic_key", ""),
                "type": frontmatter.get("type", "note"),
                "status": frontmatter.get("status", "active"),
                "source": frontmatter.get("source", ""),
                "created": frontmatter.get("created", ""),
                "updated": frontmatter.get("updated", ""),
                "tags": frontmatter.get("tags", []),
                "project": frontmatter.get("project", ""),
                "confidence": frontmatter.get("confidence", 0),
                "summary": body_data.get("summary", ""),
                "key_points": body_data.get("key_points", []),
                "action_items": body_data.get("action_items", []),
                "decisions": body_data.get("decisions", []),
                "people_mentioned": body_data.get("people_mentioned", []),
                "projects_mentioned": body_data.get("projects_mentioned", []),
                "related": frontmatter.get("related", []),
                "supersedes": frontmatter.get("supersedes", []),
                "superseded_by": frontmatter.get("superseded_by", ""),
                "file": str(f),
            },
            text,
        ))
    return entries


def _score_entry(entry: dict[str, Any], text: str, query: str) -> int:
    query_lower = query.strip().lower()
    if not query_lower:
        return 1

    score = 0
    topic_key = str(entry.get("topic_key", "")).lower()
    summary = str(entry.get("summary", "")).lower()
    tags = [str(tag).lower() for tag in entry.get("tags", [])]
    key_points = [str(point).lower() for point in entry.get("key_points", [])]

    if topic_key == query_lower:
        score += 100
    elif query_lower in topic_key:
        score += 40
    if query_lower in summary:
        score += 30
    if any(query_lower == tag for tag in tags):
        score += 25
    elif any(query_lower in tag for tag in tags):
        score += 15
    if any(query_lower in point for point in key_points):
        score += 8
    if query_lower in text.lower():
        score += 1
    return score


def _brief_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry.get("id", ""),
        "topic_key": entry.get("topic_key", ""),
        "type": entry.get("type", "note"),
        "status": entry.get("status", "active"),
        "summary": entry.get("summary", ""),
        "tags": entry.get("tags", []),
    }


def _resolve_related(entries: list[dict[str, Any]], identifiers: Any) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    if isinstance(identifiers, str):
        identifiers = [identifiers]
    if not isinstance(identifiers, list):
        return resolved
    for identifier in identifiers:
        if not identifier:
            continue
        for entry in entries:
            if identifier in {entry.get("id"), entry.get("topic_key")} or identifier in Path(entry["file"]).stem:
                resolved.append(_brief_entry(entry))
                break
    return resolved


def _split_frontmatter_body(text: str) -> tuple[dict[str, Any] | None, str]:
    """Return parsed frontmatter and body text after either supported frontmatter format."""
    json_match = re.search(r"^---\s*(\{.*?\})\s*---\s*", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1)), text[json_match.end():]
        except json.JSONDecodeError:
            return None, text

    yaml_match = re.search(r"^---\n(.*?)\n---\s*", text, re.DOTALL)
    if yaml_match:
        return _extract_frontmatter(text), text[yaml_match.end():]

    return None, text


def _format_with_frontmatter(frontmatter: dict[str, Any], body_text: str) -> str:
    frontmatter["updated"] = __import__("time").strftime("%Y-%m-%d")
    fm_json = json.dumps(frontmatter, ensure_ascii=False, indent=2)
    return f"---{fm_json}---\n\n{body_text.lstrip()}"


def _find_entry_file(topic_id: str) -> Path | None:
    if not PROCESSED.exists():
        return None

    for f in PROCESSED.rglob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        frontmatter = _extract_frontmatter(text)
        if not frontmatter:
            continue
        if (
            topic_id == frontmatter.get("id")
            or topic_id == frontmatter.get("topic_key")
            or topic_id in f.stem
        ):
            return f
    return None


def _update_entry_frontmatter(topic_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    entry_file = _find_entry_file(topic_id)
    if not entry_file:
        return {"updated": False, "error": "not_found", "topic_id": topic_id}

    text = entry_file.read_text(encoding="utf-8")
    frontmatter, body_text = _split_frontmatter_body(text)
    if not frontmatter:
        return {"updated": False, "error": "invalid_frontmatter", "path": str(entry_file)}

    frontmatter.update(updates)
    tmp = entry_file.with_suffix(".md.tmp")
    tmp.write_text(_format_with_frontmatter(frontmatter, body_text), encoding="utf-8")
    tmp.replace(entry_file)
    return {
        "updated": True,
        "id": frontmatter.get("id"),
        "topic_key": frontmatter.get("topic_key", ""),
        "status": frontmatter.get("status", "active"),
        "path": str(entry_file),
    }


# ============================================================
# MCP JSON-RPC 处理器
# ============================================================

_TOOL_HANDLERS: dict[str, Any] = {
    "sidebrain_search": tool_sidebrain_search,
    "sidebrain_ingest": tool_sidebrain_ingest,
    "sidebrain_list_projects": tool_sidebrain_list_projects,
    "sidebrain_get": tool_sidebrain_get,
    "sidebrain_update": tool_sidebrain_update,
    "sidebrain_resolve": tool_sidebrain_resolve,
    "sidebrain_supersede": tool_sidebrain_supersede,
}

# MCP 工具定义（返回给 Pi 用于发现）
_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "sidebrain_search",
        "description": "搜索 sidebrain 记忆库中已处理的条目",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "limit": {"type": "integer", "description": "最大返回条数", "default": 10},
                "status": {"type": "string", "description": "状态过滤：active/resolved/superseded/archived/all", "default": "active"},
                "type": {"type": "string", "description": "记忆类型过滤，如 decision/problem-solution/gotcha/note"},
                "topic_key": {"type": "string", "description": "按稳定主题键精确过滤"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "sidebrain_ingest",
        "description": "立即将文本摄入到 sidebrain 知识库",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要摄入的文本内容"},
                "source": {"type": "string", "description": "来源标识", "default": "ad-hoc"},
                "summary": {"type": "string", "description": "摘要；提供时进入结构化模式并直接写 processed"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "action_items": {"type": "array", "items": {"type": "string"}},
                "decisions": {"type": "array"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "topic_key": {"type": "string", "description": "主题稳定键；相同 active topic_key 会更新旧记忆"},
                "type": {"type": "string", "default": "note"},
                "status": {"type": "string", "default": "active"},
                "confidence": {"type": "number"},
            },
        },
    },
    {
        "name": "sidebrain_list_projects",
        "description": "列出 sidebrain 中所有项目",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "sidebrain_get",
        "description": "获取单条记忆的详细信息",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic_id": {"type": "string", "description": "记忆 ID 或文件名关键词"},
            },
            "required": ["topic_id"],
        },
    },
    {
        "name": "sidebrain_update",
        "description": "更新单条记忆的元数据字段",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic_id": {"type": "string", "description": "记忆 ID、topic_key 或文件名关键词"},
                "fields": {"type": "object", "description": "允许更新 topic_key/type/status/confidence/tags/project/related/supersedes"},
            },
            "required": ["topic_id", "fields"],
        },
    },
    {
        "name": "sidebrain_resolve",
        "description": "将记忆标记为 resolved，使其默认不再出现在搜索结果中",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic_id": {"type": "string", "description": "记忆 ID、topic_key 或文件名关键词"},
                "reason": {"type": "string", "description": "处理原因"},
            },
            "required": ["topic_id"],
        },
    },
    {
        "name": "sidebrain_supersede",
        "description": "将记忆标记为 superseded",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic_id": {"type": "string", "description": "记忆 ID、topic_key 或文件名关键词"},
                "superseded_by": {"type": "string", "description": "替代它的新记忆 ID 或 topic_key"},
                "reason": {"type": "string", "description": "替代原因"},
            },
            "required": ["topic_id"],
        },
    },
]


def _make_error(code: int, message: str, request_id: str | None = None) -> str:
    """生成 JSON-RPC 错误响应。"""
    response = {
        "jsonrpc": JSONRPC_VERSION,
        "error": {"code": code, "message": message},
    }
    if request_id is not None:
        response["id"] = request_id
    return json.dumps(response, ensure_ascii=False)


def _make_success(result: Any, request_id: str) -> str:
    """生成 JSON-RPC 成功响应。"""
    return json.dumps(
        {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result},
        ensure_ascii=False,
    )


def handle_request(request: dict) -> str:
    """处理单条 JSON-RPC 请求。

    Args:
        request: 解析后的 JSON-RPC 请求。

    Returns:
        JSON-RPC 响应字符串。
    """
    req_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params", {})

    if method == "initialize":
        return _make_success(
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "sidebrain", "version": "0.1.0"},
            },
            req_id,
        )

    if method == "notifications/initialized":
        # 不需要响应
        return ""

    if method == "tools/list":
        return _make_success({"tools": _TOOL_DEFINITIONS}, req_id)

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        handler = _TOOL_HANDLERS.get(tool_name)
        if not handler:
            return _make_error(-32601, f"Tool not found: {tool_name}", req_id)

        try:
            result = handler(**tool_args)
            return _make_success(result, req_id)
        except Exception as e:
            logger.exception("Tool %s failed", tool_name)
            return _make_error(-32603, f"Tool error: {e}", req_id)

    return _make_error(-32601, f"Method not found: {method}", req_id)


def serve_stdio() -> None:
    """通过 stdio 启动 MCP server。

    从 stdin 读取 JSON-RPC 请求（每行一个），
    将 JSON-RPC 响应写入 stdout。
    """
    logger.info("Starting MCP server (stdio)")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            response = _make_error(-32700, f"Parse error: {e}")
            sys.stdout.write(response + "\n")
            sys.stdout.flush()
            continue

        if isinstance(request, list):
            # 批处理请求
            responses = [handle_request(req) for req in request]
            # 过滤空响应
            responses = [r for r in responses if r]
            if responses:
                sys.stdout.write("\n".join(responses) + "\n")
                sys.stdout.flush()
        else:
            response = handle_request(request)
            if response:
                sys.stdout.write(response + "\n")
                sys.stdout.flush()
