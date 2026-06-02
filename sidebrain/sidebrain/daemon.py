"""守护进程 — 定时增量摄入 + 处理 + 同步。

每隔 interval_sec 秒执行一次完整 pipeline：
1. Ingest Pi sessions
2. Ingest meetings
3. Process raw → processed
4. Sync to Pi mirror
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from sidebrain.config import load_config
from sidebrain.ingest.meeting_watcher import ingest_meetings
from sidebrain.ingest.pi_watcher import ingest_pi_sessions
from sidebrain.paths import STATE
from sidebrain.process_pipeline import process_all
from sidebrain.sync.pi_mirror import sync_to_pi

logger = logging.getLogger(__name__)

PID_FILE = STATE / "daemon.pid"
LOCK_FILE = STATE / "daemon.lock"
LOG_FILE = STATE.parent / "logs" / "daemon.log"


def _write_pid() -> None:
    """写入 PID 文件。"""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def _remove_pid() -> None:
    """删除 PID 文件。"""
    PID_FILE.unlink(missing_ok=True)


def _is_running() -> bool:
    """检查 daemon 是否已在运行。"""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            # 检查进程是否存在
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, ValueError, OSError):
            _remove_pid()
    return False


def _setup_signal_handlers() -> None:
    """设置信号处理器。"""
    stop_event = threading.Event()

    def _handle_stop(signum, frame):
        logger.info("Received signal %d, stopping...", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    return stop_event


def _run_once(cfg: dict) -> dict[str, Any]:
    """单次 pipeline 执行。"""
    results: dict[str, Any] = {}

    # 1. Ingest Pi sessions
    if cfg["ingest"]["pi"]["enabled"]:
        try:
            pi_result = ingest_pi_sessions(
                batch_size=cfg["ingest"]["pi"]["batch_size"],
                max_size_mb=cfg["ingest"]["pi"]["max_size_mb"],
            )
            results["ingest_pi"] = pi_result
        except Exception as e:
            logger.error("Pi ingest failed: %s", e)
            results["ingest_pi"] = {"ingested": 0, "error": str(e)}

    # 2. Ingest meetings
    if cfg["ingest"]["meetings"]["enabled"]:
        try:
            meeting_result = ingest_meetings(
                batch_size=cfg["ingest"]["meetings"]["batch_size"],
            )
            results["ingest_meetings"] = meeting_result
        except Exception as e:
            logger.error("Meeting ingest failed: %s", e)
            results["ingest_meetings"] = {"ingested": 0, "error": str(e)}

    # 3. Process
    try:
        process_result = process_all()
        results["process"] = process_result
    except Exception as e:
        logger.error("Process failed: %s", e)
        results["process"] = {"written": 0, "error": str(e)}

    # 4. Sync
    try:
        sync_result = sync_to_pi(
            tags_filter=cfg["sync"]["tags_filter"],
            max_items=cfg["sync"]["max_items"],
        )
        results["sync"] = sync_result
    except Exception as e:
        logger.error("Sync failed: %s", e)
        results["sync"] = {"synced": 0, "error": str(e)}

    return results


# 需要在 import 后设置 threading
import threading  # noqa: E402


def start_daemon(interval_sec: int = 300) -> None:
    """启动守护进程。

    Args:
        interval_sec: 轮询间隔（秒）。
    """
    if _is_running():
        logger.error("Daemon is already running (PID: %s)", PID_FILE.read_text().strip())
        print("Daemon is already running")
        sys.exit(1)

    _write_pid()
    logger.info("Daemon started (PID: %d, interval: %ds)", os.getpid(), interval_sec)
    print(f"Daemon started (PID: {os.getpid()}, interval: {interval_sec}s)")

    cfg = load_config()
    stop_event = _setup_signal_handlers()

    # 首次立即运行
    logger.info("Running initial pipeline...")
    results = _run_once(cfg)
    logger.info("Initial pipeline complete: %s", results)

    while not stop_event.is_set():
        logger.debug("Waiting %d seconds...", interval_sec)
        stop_event.wait(interval_sec)

        if stop_event.is_set():
            break

        logger.info("Running pipeline...")
        results = _run_once(cfg)
        logger.info("Pipeline complete: %s", results)

    _remove_pid()
    logger.info("Daemon stopped")
    print("Daemon stopped")


def stop_daemon() -> bool:
    """停止守护进程。

    Returns:
        是否成功发送停止信号。
    """
    if not PID_FILE.exists():
        logger.warning("Daemon PID file not found")
        return False

    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        logger.info("Stop signal sent to PID %d", pid)
        return True
    except (ProcessLookupError, ValueError, OSError) as e:
        logger.error("Failed to stop daemon: %s", e)
        _remove_pid()
        return False


def daemon_status() -> dict[str, Any]:
    """获取 daemon 状态。"""
    if not PID_FILE.exists():
        return {"running": False, "pid": None}

    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return {"running": True, "pid": pid}
    except (ProcessLookupError, ValueError, OSError):
        _remove_pid()
        return {"running": False, "pid": None}
