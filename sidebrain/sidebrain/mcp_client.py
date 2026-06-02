"""MCP 客户端 — Python 端调用 sidebrain MCP Server。

通过子进程启动 Node.js MCP server，用 stdio JSON-RPC 通信。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MCP_SERVER_PATH = Path.home() / ".pi" / "agent" / "extensions" / "sidebrain-mcp-server.mjs"
MCP_SERVER_NODE = "node"

# 全局共享进程（单例）
_process: subprocess.Popen | None = None


def _ensure_server() -> subprocess.Popen:
    """确保 MCP server 子进程在运行。"""
    global _process
    if _process is not None and _process.poll() is None:
        return _process

    if not MCP_SERVER_PATH.exists():
        raise FileNotFoundError(f"MCP server not found: {MCP_SERVER_PATH}")

    _process = subprocess.Popen(
        [MCP_SERVER_NODE, str(MCP_SERVER_PATH)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    # 先初始化
    _send_request("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "ga-sidebrain", "version": "0.1.0"},
    })
    _send_notification("notifications/initialized")

    logger.info("MCP server started (PID: %s)", _process.pid)
    return _process


def _send_request(method: str, params: dict) -> dict:
    """发送 JSON-RPC 请求并等待响应。"""
    proc = _ensure_server()
    req_id = id(method)  # 简单 ID 生成

    request = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params,
    }

    line = json.dumps(request, ensure_ascii=False)
    proc.stdin.write(line + "\n")
    proc.stdin.flush()

    # 读取响应（读一行）
    response_line = proc.stdout.readline()
    if not response_line:
        raise ConnectionError("MCP server closed connection")

    try:
        response = json.loads(response_line.strip())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON-RPC response: {response_line[:200]}") from e

    if "error" in response:
        err = response["error"]
        raise RuntimeError(f"MCP error (code={err.get('code')}): {err.get('message', 'unknown')}")

    return response.get("result", {})


def _send_notification(method: str, params: dict | None = None) -> None:
    """发送 JSON-RPC 通知（无 ID，不等待响应）。"""
    proc = _ensure_server()

    notification = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params:
        notification["params"] = params

    proc.stdin.write(json.dumps(notification, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def close() -> None:
    """关闭 MCP server 连接。"""
    global _process
    if _process is not None and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _process.kill()
        _process = None
        logger.info("MCP server stopped")


# ============================================================
# 工具接口
# ============================================================


def search(query: str, limit: int = 10) -> str:
    """搜索 sidebrain 知识库，返回文本。"""
    result = _send_request("tools/call", {
        "name": "sidebrain_search",
        "arguments": {"query": query, "limit": limit},
    })
    return _extract_text(result)


def get_detail(topic_id: str) -> str | None:
    """获取单条记忆详情。"""
    result = _send_request("tools/call", {
        "name": "sidebrain_get",
        "arguments": {"topic_id": topic_id},
    })
    text = _extract_text(result)
    return text or None


def _extract_text(result: dict) -> str:
    """从 MCP 响应中提取文本。"""
    content = result.get("content", [])
    texts = [c.get("text", "") for c in content if c.get("type") == "text"]
    return "\n".join(texts)


def list_projects() -> list[str]:
    """列出所有项目。"""
    result = _send_request("tools/call", {
        "name": "sidebrain_list_projects",
        "arguments": {},
    })
    text = _extract_text(result)
    # 解析 "可用项目:\n  - xxx\n  - yyy" 格式
    lines = text.strip().split("\n")
    projects = []
    for line in lines:
        line = line.strip()
        if line.startswith("- "):
            projects.append(line[2:])
    return projects


def ingest(text: str, source: str = "ga") -> dict:
    """摄入文本到 sidebrain。"""
    result = call_tool("sidebrain_ingest", {"text": text, "source": source})
    resp_text = _extract_text(result)
    import re
    m = re.search(r'ID: ([a-f0-9-]+)', resp_text)
    return {"id": m.group(1) if m else None, "text": resp_text}


def call_tool(name: str, arguments: dict) -> dict:
    """通用 MCP 工具调用。"""
    return _send_request("tools/call", {"name": name, "arguments": arguments})
