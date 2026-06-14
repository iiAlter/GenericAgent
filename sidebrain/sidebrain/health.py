"""健康检查 — 知识库完整性、依赖可用性。"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from sidebrain.paths import (
    KNOWLEDGE,
    LOGS,
    PROCESSED,
    QUARANTINE,
    RAW_AD_HOC,
    RAW_MEETINGS,
    RAW_PI,
    STATE,
)

logger = logging.getLogger(__name__)


def _check_dir(dir_path: Path, label: str) -> dict[str, Any]:
    """检查目录状态。"""
    if not dir_path.exists():
        return {"label": label, "status": "missing", "files": 0}

    if dir_path in {LOGS, STATE}:
        files = [p for p in dir_path.glob("*") if p.is_file()]
    else:
        files = list(dir_path.rglob("*.md"))
    return {
        "label": label,
        "status": "ok",
        "files": len(files),
        "path": str(dir_path),
    }


def _systemd_unit_state(unit: str) -> dict[str, Any]:
    """Return user systemd unit state if systemd is available."""
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "show", unit, "--property=LoadState,ActiveState,SubState,NextElapseUSecRealtime"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"available": False, "unit": unit, "error": str(e)}

    if proc.returncode != 0:
        return {
            "available": False,
            "unit": unit,
            "error": proc.stderr.strip() or proc.stdout.strip(),
        }

    data: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value

    return {
        "available": True,
        "unit": unit,
        "load_state": data.get("LoadState", ""),
        "active_state": data.get("ActiveState", ""),
        "sub_state": data.get("SubState", ""),
        "next_elapse": data.get("NextElapseUSecRealtime", ""),
    }


def check_all() -> dict[str, Any]:
    """全面健康检查。"""
    checks: list[dict[str, Any]] = []

    # 数据目录
    checks.append(_check_dir(RAW_PI, "raw/pi"))
    checks.append(_check_dir(RAW_MEETINGS, "raw/meetings"))
    checks.append(_check_dir(RAW_AD_HOC, "raw/ad_hoc"))
    checks.append(_check_dir(PROCESSED, "processed"))
    checks.append(_check_dir(QUARANTINE, "quarantine"))
    checks.append(_check_dir(STATE, "state"))
    checks.append(_check_dir(LOGS, "logs"))

    # 文件大小统计
    total_size = 0
    if KNOWLEDGE.exists():
        for f in KNOWLEDGE.rglob("*"):
            if f.is_file():
                try:
                    total_size += f.stat().st_size
                except OSError:
                    pass

    # 游标状态
    cursor_info = {}
    for cursor_file in STATE.glob("*cursor*.json"):
        try:
            data = json.loads(cursor_file.read_text())
            cursor_info[cursor_file.stem] = {
                "last_run": data.get("last_run", "N/A"),
                "entries": len(data.get("processed_hashes", data)),
            }
        except (json.JSONDecodeError, OSError):
            pass

    # Daemon 状态
    from sidebrain.daemon import daemon_status
    from sidebrain.process_pipeline import get_backlog

    daemon_st = daemon_status()
    systemd = {
        "mcp": _systemd_unit_state("sidebrain-mcp.service"),
        "process_timer": _systemd_unit_state("sidebrain-process.timer"),
        "process_service": _systemd_unit_state("sidebrain-process.service"),
    }
    backlog = get_backlog()

    # 总体状态
    has_errors = any(c["status"] == "missing" for c in checks)
    has_quarantine = checks[4]["files"] > 0 if len(checks) > 4 else False
    warnings: list[str] = []
    if backlog["pending"] > 0:
        warnings.append(
            f"process backlog has {backlog['pending']} pending files "
            f"({backlog['rounds_remaining']} rounds at batch_size={backlog['batch_size']})"
        )
    timer = systemd["process_timer"]
    if timer.get("available") and timer.get("active_state") != "active":
        warnings.append("sidebrain-process.timer is not active")
    mcp = systemd["mcp"]
    if mcp.get("available") and mcp.get("active_state") != "active":
        warnings.append("sidebrain-mcp.service is not active")

    return {
        "checks": checks,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "cursor_info": cursor_info,
        "daemon": daemon_st,
        "systemd": systemd,
        "backlog": backlog,
        "healthy": not has_errors and not has_quarantine,
        "warnings": warnings,
    }


def print_health_report() -> str:
    """打印可读的健康报告。"""
    report = check_all()

    lines = ["=== Sidebrain Health Report ===", ""]

    lines.append("--- Directories ---")
    for c in report["checks"]:
        icon = "✓" if c["status"] == "ok" else "✗"
        lines.append(f"  {icon} {c['label']}: {c['files']} files")

    lines.append("")
    lines.append(f"--- Storage ---")
    lines.append(f"  Total: {report['total_size_mb']} MB")

    lines.append("")
    lines.append(f"--- Cursors ---")
    for name, info in report["cursor_info"].items():
        lines.append(f"  {name}: last_run={info.get('last_run', 'N/A')}")

    lines.append("")
    lines.append(f"--- Daemon ---")
    daemon = report["daemon"]
    if daemon["running"]:
        lines.append(f"  ✓ Running (PID: {daemon['pid']})")
    else:
        lines.append(f"  ○ Not running")

    lines.append("")
    lines.append("--- Systemd ---")
    for label, unit in [
        ("MCP HTTP", report["systemd"]["mcp"]),
        ("Process timer", report["systemd"]["process_timer"]),
        ("Process service", report["systemd"]["process_service"]),
    ]:
        if not unit.get("available"):
            lines.append(f"  ○ {label}: unavailable ({unit.get('error', 'unknown')})")
            continue
        active = unit.get("active_state", "?")
        sub = unit.get("sub_state", "?")
        icon = "✓" if active == "active" else "○"
        lines.append(f"  {icon} {label}: {active}/{sub}")

    lines.append("")
    lines.append("--- Backlog ---")
    backlog = report["backlog"]
    lines.append(f"  Pending: {backlog['pending']}")
    lines.append(f"  Batch size: {backlog['batch_size']}")
    lines.append(f"  Rounds remaining: {backlog['rounds_remaining']}")

    if report["warnings"]:
        lines.append("")
        lines.append("--- Warnings ---")
        for warning in report["warnings"]:
            lines.append(f"  - {warning}")

    lines.append("")
    if report["healthy"]:
        lines.append("✓ All checks passed")
    else:
        lines.append("✗ Some checks failed")
        for c in report["checks"]:
            if c["status"] == "missing":
                lines.append(f"  - {c['label']}: missing")

    return "\n".join(lines)
