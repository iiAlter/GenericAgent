"""MCP 安装器 — 注册 sidebrain MCP server 到 Pi settings.json。

修改 ~/.pi/agent/settings.json，新增 mcpServers 字段。
安装前备份原始文件到 settings.json.bak.<timestamp>。
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PI_SETTINGS = Path.home() / ".pi" / "agent" / "settings.json"

# sidebrain MCP server 配置
SIDEBRAIN_MCP_CONFIG: dict[str, Any] = {
    "sidebrain": {
        "command": "python3",
        "args": ["-m", "sidebrain", "mcp"],
        "env": {},
    }
}


def _backup_settings() -> Path | None:
    """备份 settings.json。"""
    if not PI_SETTINGS.exists():
        return None

    ts = time.strftime("%Y%m%d%H%M%S")
    backup = PI_SETTINGS.with_suffix(f".json.bak.{ts}")
    shutil.copy2(PI_SETTINGS, backup)
    logger.info("Backup saved: %s", backup)
    return backup


def install() -> dict[str, Any]:
    """安装 sidebrain MCP server 到 Pi 设置。

    Returns:
        安装结果。
    """
    if not PI_SETTINGS.exists():
        return {"success": False, "error": f"Pi settings not found: {PI_SETTINGS}"}

    # 备份
    backup = _backup_settings()

    try:
        settings = json.loads(PI_SETTINGS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"success": False, "error": f"Failed to read settings: {e}"}

    # 添加/更新 mcpServers
    if "mcpServers" not in settings:
        settings["mcpServers"] = {}

    old_config = settings["mcpServers"].get("sidebrain")
    settings["mcpServers"]["sidebrain"] = SIDEBRAIN_MCP_CONFIG["sidebrain"]

    # 写入
    try:
        tmp = PI_SETTINGS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(PI_SETTINGS)
    except OSError as e:
        return {"success": False, "error": f"Failed to write settings: {e}"}

    return {
        "success": True,
        "backup": str(backup) if backup else None,
        "changed": old_config != SIDEBRAIN_MCP_CONFIG["sidebrain"],
    }


def uninstall() -> dict[str, Any]:
    """从 Pi 设置中移除 sidebrain MCP server。"""
    if not PI_SETTINGS.exists():
        return {"success": False, "error": f"Pi settings not found: {PI_SETTINGS}"}

    backup = _backup_settings()

    try:
        settings = json.loads(PI_SETTINGS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"success": False, "error": f"Failed to read settings: {e}"}

    if "mcpServers" in settings and "sidebrain" in settings["mcpServers"]:
        del settings["mcpServers"]["sidebrain"]
        if not settings["mcpServers"]:
            del settings["mcpServers"]

        try:
            tmp = PI_SETTINGS.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(PI_SETTINGS)
        except OSError as e:
            return {"success": False, "error": f"Failed to write settings: {e}"}

    return {"success": True, "backup": str(backup) if backup else None}


def status() -> dict[str, Any]:
    """检查 MCP server 安装状态。

    Returns:
        {"installed": bool, "config": dict|None}
    """
    if not PI_SETTINGS.exists():
        return {"installed": False, "config": None}

    try:
        settings = json.loads(PI_SETTINGS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"installed": False, "config": None}

    mcp_settings = settings.get("mcpServers", {})
    sidebrain_cfg = mcp_settings.get("sidebrain")

    return {
        "installed": sidebrain_cfg is not None,
        "config": sidebrain_cfg,
    }
