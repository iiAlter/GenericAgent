"""健康检查 — 知识库完整性、镜像同步状态、依赖可用性。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sidebrain.paths import (
    KNOWLEDGE,
    LOGS,
    PI_MEMORIES_MIRROR,
    PI_RULES_MIRROR,
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

    files = list(dir_path.rglob("*.md")) if dir_path != LOGS else list(dir_path.glob("*"))
    return {
        "label": label,
        "status": "ok",
        "files": len(files),
        "path": str(dir_path),
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

    # Pi 镜像
    checks.append(_check_dir(PI_MEMORIES_MIRROR, "mirror/memories"))
    checks.append(_check_dir(PI_RULES_MIRROR, "mirror/rules"))

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

    daemon_st = daemon_status()

    # 总体状态
    has_errors = any(c["status"] == "missing" for c in checks)
    has_quarantine = checks[4]["files"] > 0 if len(checks) > 4 else False

    return {
        "checks": checks,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "cursor_info": cursor_info,
        "daemon": daemon_st,
        "healthy": not has_errors and not has_quarantine,
        "warnings": [],
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
    if report["healthy"]:
        lines.append("✓ All checks passed")
    else:
        lines.append("✗ Some checks failed")
        for c in report["checks"]:
            if c["status"] == "missing":
                lines.append(f"  - {c['label']}: missing")

    return "\n".join(lines)
