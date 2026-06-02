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
import sys
from pathlib import Path
from typing import Any

from sidebrain.ingest.ad_hoc import ingest_text
from sidebrain.paths import PROCESSED
from sidebrain.process.dedup import list_projects

logger = logging.getLogger(__name__)

# MCP JSON-RPC 协议常量
JSONRPC_VERSION = "2.0"


# ============================================================
# 工具实现
# ============================================================


def tool_sidebrain_search(query: str, limit: int = 10) -> dict[str, Any]:
    """搜索处理后的记忆条目。

    Args:
        query: 搜索关键词。
        limit: 最大返回条数，默认 10。

    Returns:
        {"entries": [...]}
    """
    if not PROCESSED.exists():
        return {"entries": []}

    query_lower = query.lower()
    results: list[dict[str, Any]] = []

    for f in sorted(PROCESSED.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue

        # 搜索 frontmatter + body
        if query_lower not in text.lower():
            continue

        # 提取 frontmatter
        frontmatter = _extract_frontmatter(text)
        if not frontmatter:
            continue

        # 提取 body
        body_data = _extract_body(text)

        entry = {
            "id": frontmatter.get("id", ""),
            "source": frontmatter.get("source", ""),
            "created": frontmatter.get("created", ""),
            "tags": frontmatter.get("tags", []),
            "project": frontmatter.get("project", ""),
            "confidence": frontmatter.get("confidence", 0),
            "summary": body_data.get("summary", ""),
            "key_points": body_data.get("key_points", []),
            "action_items": body_data.get("action_items", []),
            "file": str(f),
        }
        results.append(entry)

        if len(results) >= limit:
            break

    return {"entries": results}


def tool_sidebrain_ingest(text: str, source: str = "ad-hoc") -> dict[str, Any]:
    """立即摄入文本。

    Args:
        text: 要摄入的文本内容。
        source: 来源标识。

    Returns:
        摄入结果。
    """
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

    for f in PROCESSED.rglob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue

        frontmatter = _extract_frontmatter(text)
        if not frontmatter:
            continue

        entry_id = frontmatter.get("id", "")
        if topic_id == entry_id or topic_id in f.stem:
            body_data = _extract_body(text)
            return {
                "entry": {
                    "id": entry_id,
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
                }
            }

    return {"entry": None}


# ============================================================
# Helper
# ============================================================


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


def _extract_body(text: str) -> dict:
    """提取 body JSON。"""
    import re
    match = re.search(r"```json\n(.*?)\n```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return {}


# ============================================================
# MCP JSON-RPC 处理器
# ============================================================

_TOOL_HANDLERS: dict[str, Any] = {
    "sidebrain_search": tool_sidebrain_search,
    "sidebrain_ingest": tool_sidebrain_ingest,
    "sidebrain_list_projects": tool_sidebrain_list_projects,
    "sidebrain_get": tool_sidebrain_get,
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
            },
            "required": ["text"],
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
