"""处理 pipeline — 扫描 raw/ 下新数据，通知 GA 处理。

流程：
1. 扫描 raw/ 下所有未处理的文件
2. 对每个文件创建 GA task，让 GA 分析并输出结构化 JSON
3. Python 读取 JSON 后直接调 sidebrain_ingest 入库
（绕过 LLM 工具调用限制，Python 端直接操作）
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from sidebrain.config import load_config
from sidebrain.paths import GA_ROOT, PROCESSED, RAW_PI, RAW_MEETINGS, RAW_AD_HOC, RAW_GA, STATE
from sidebrain.process.validate import normalize_extraction, write_validation_quarantine
from sidebrain.process.writer import write_processed

logger = logging.getLogger(__name__)

CURSOR_FILE = STATE / "process_cursor.json"
GA_AGENT = GA_ROOT / "agentmain.py"


def _load_cursor() -> dict:
    if CURSOR_FILE.exists():
        try:
            return json.loads(CURSOR_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"processed_hashes": [], "last_run": None}


def _save_cursor(cursor: dict) -> None:
    tmp = CURSOR_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cursor, indent=2))
    with tmp.open("rb") as f:
        os.fsync(f.fileno())
    tmp.replace(CURSOR_FILE)


def _raw_dirs() -> list[tuple[Path, str]]:
    """Return configured raw directories, including dynamically discovered agent dirs."""
    raw_dirs: list[tuple[Path, str]] = [
        (RAW_PI, "pi_session"),
        (RAW_MEETINGS, "meeting"),
        (RAW_AD_HOC, "ad_hoc"),
        (RAW_GA, "ga_session"),
    ]
    raw_base = RAW_PI.parent
    if raw_base.exists():
        known = {RAW_PI, RAW_MEETINGS, RAW_AD_HOC, RAW_GA}
        for sub in raw_base.iterdir():
            if sub.is_dir() and sub not in known:
                raw_dirs.append((sub, sub.name))
    return raw_dirs


def _raw_files(raw_dir: Path, source_type: str) -> list[Path]:
    files = sorted(raw_dir.rglob("*.md"))
    if source_type == "pi_session":
        files += sorted(raw_dir.rglob("*.jsonl"))
    return files


def _content_hash(path: Path) -> str:
    return __import__("hashlib").sha256(path.read_bytes()).hexdigest()


def get_backlog(batch_size: int | None = None) -> dict[str, Any]:
    """Summarize process backlog without mutating cursor state."""
    if batch_size is None:
        cfg = load_config()
        batch_size = int(cfg.get("process", {}).get("batch_size", 10))

    cursor = _load_cursor()
    processed_hashes = set(cursor.get("processed_hashes", []))
    by_source: dict[str, dict[str, int]] = {}
    total = skipped = pending = 0

    for raw_dir, source_type in _raw_dirs():
        if not raw_dir.exists():
            continue
        files = _raw_files(raw_dir, source_type)
        source_stats = {"total": 0, "processed": 0, "pending": 0}
        for path in files:
            total += 1
            source_stats["total"] += 1
            try:
                is_processed = _content_hash(path) in processed_hashes
            except OSError:
                continue
            if is_processed:
                skipped += 1
                source_stats["processed"] += 1
            else:
                pending += 1
                source_stats["pending"] += 1
        by_source[raw_dir.name] = source_stats

    rounds_remaining = 0
    if batch_size and batch_size > 0:
        rounds_remaining = (pending + batch_size - 1) // batch_size

    return {
        "total": total,
        "processed": skipped,
        "pending": pending,
        "batch_size": batch_size,
        "rounds_remaining": rounds_remaining,
        "by_source": by_source,
    }


def _update_process_metrics(result: dict[str, Any]) -> None:
    """Update shared metrics after a process run."""
    metrics_file = CURSOR_FILE.parent / "metrics.json"
    metrics: dict[str, Any] = {}
    if metrics_file.exists():
        try:
            metrics = json.loads(metrics_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    metrics["last_process_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    metrics["last_process_total"] = result.get("total", 0)
    metrics["last_process_dispatched"] = result.get("dispatched", 0)
    metrics["last_process_skipped"] = result.get("skipped", 0)
    metrics["last_process_deferred"] = result.get("deferred", 0)
    metrics["last_process_errors"] = result.get("errors", 0)

    metrics_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = metrics_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(metrics, indent=2))
    with tmp.open("rb") as f:
        os.fsync(f.fileno())
    tmp.replace(metrics_file)


def _call_ga(task_name: str, prompt: str) -> dict:
    """调用 GA 处理一个任务。

    Args:
        task_name: 任务目录名。
        prompt: 给 GA 的 prompt。

    Returns:
        执行结果。
    """
    import hashlib
    task_dir = GA_ROOT / "temp" / task_name
    task_dir.mkdir(parents=True, exist_ok=True)

    # 写 input.txt
    input_file = task_dir / "input.txt"
    input_file.write_text(prompt, encoding="utf-8")

    # 启动 GA 任务
    cmd = [
        sys.executable,
        str(GA_AGENT),
        "--task", task_name,
        "--nobg",
    ]

    try:
        logger.info("Calling GA: task=%s", task_name)
        proc = subprocess.Popen(
            cmd,
            cwd=str(GA_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=180)
            success = proc.returncode == 0
        except subprocess.TimeoutExpired:
            # GA 超时，杀进程组后继续（JSON 可能已生成）
            try:
                os.killpg(os.getpgid(proc.pid), __import__('signal').SIGKILL)
            except Exception:
                proc.kill()
            stdout, stderr = proc.communicate()
            success = False  # 超时算 failure，但 process_all 会检查 JSON

        # 读取输出文件
        output_files = sorted(task_dir.glob("output*.txt"))
        outputs = []
        for f in output_files:
            try:
                outputs.append(f.read_text(encoding="utf-8"))
            except Exception:
                pass

        return {
            "success": success,
            "output": "\n".join(outputs) if outputs else stdout,
            "stderr": stderr,
            "returncode": proc.returncode,
        }
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}


def _safe_identifier(value: str, max_len: int = 80) -> str:
    """Return a filesystem-safe identifier while keeping it recognizable."""
    safe = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("._-")
    return (safe or "untitled")[:max_len]


def process_all(dry_run: bool = False, batch_size: int | None = None) -> dict[str, Any]:
    """扫描 raw/ 下未处理数据，逐个创建 GA 任务。

    Args:
        dry_run: 仅模拟，不实际调用 GA。

    Returns:
        处理统计。
    """
    cfg = load_config()
    effective_batch_size = int(batch_size if batch_size is not None else cfg.get("process", {}).get("batch_size", 10))
    remaining_budget = max(effective_batch_size, 0)
    cursor = _load_cursor()
    processed_hashes = set(cursor.get("processed_hashes", []))

    # 扫描所有 raw 子目录（pi/meetings/ad_hoc/ga 以及第三方 agent 目录）
    raw_dirs = _raw_dirs()

    total = 0
    dispatched = 0
    skipped = 0
    deferred = 0
    errors = 0
    new_hashes: list[str] = []

    for raw_dir, source_type in raw_dirs:
        if not raw_dir.exists():
            continue

        # 所有 raw 目录递归扫 .md；MCP ingest 可能按 agent/host 写入子目录。
        files = _raw_files(raw_dir, source_type)
        logger.info("Found %d raw files in %s", len(files), raw_dir.name)

        for f in files:
            total += 1
            source_id = f.stem

            # 检查是否已被处理
            content_hash = _content_hash(f)
            if content_hash in processed_hashes:
                skipped += 1
                continue

            if remaining_budget <= 0:
                deferred += 1
                continue

            if dry_run:
                logger.info("[DRY RUN] Would dispatch to GA: %s", source_id)
                new_hashes.append(content_hash)
                dispatched += 1
                remaining_budget -= 1
                continue

            # 构建 prompt 给 GA — 输出 JSON 文件，Python 端负责 ingest
            safe_source_id = _safe_identifier(source_id)
            json_output_path = GA_ROOT / "temp" / f"_{safe_source_id}_extracted.json"
            relative_path = str(f.relative_to(f.parents[2])) if len(f.parents) > 2 else f.name
            prompt = (
                f"请分析以下 {source_type} 数据，提取关键信息后写入 JSON 文件。\n\n"
                f"步骤：\n"
                f"1. 用 file_read 读取: {f}\n"
                f"2. 分析内容，提取摘要、关键点、行动项、决策和生命周期元数据\n"
                f"3. 用 file_write 将结果写入 JSON 文件: {json_output_path}\n\n"
                f"JSON 格式：\n"
                f"{{\n"
                f'  "summary": "一句话摘要，必填",\n'
                f'  "key_points": ["关键点1", "关键点2"],\n'
                f'  "action_items": ["行动项1", "行动项2"],\n'
                f'  "decisions": [{{"decision": "决策", "reasoning": "原因", "alternatives": ["被放弃方案"]}}],\n'
                f'  "people_mentioned": ["人名"],\n'
                f'  "projects_mentioned": ["项目名"],\n'
                f'  "tags": ["标签1", "标签2"],\n'
                f'  "type": "note|decision|problem-solution|gotcha|what-changed|trade-off",\n'
                f'  "status": "active",\n'
                f'  "topic_key": "可选，稳定主题键，如 architecture/sidebrain",\n'
                f'  "confidence": 0.8\n'
                f"}}\n\n"
                f"数据路径: {f}\n"
                f"来源类型: {source_type}\n"
                f"来源标识: {source_id}\n"
                f"JSON 输出路径: {json_output_path}\n"
            )

            task_name = f"sidebrain_{source_type}_{safe_source_id[:40]}"
            result = _call_ga(task_name, prompt)

            # 检查 JSON 输出（GA 超时时也可能已生成）
            if json_output_path.exists():
                try:
                    raw_payload = json_output_path.read_text(encoding="utf-8")
                    data = json.loads(raw_payload)
                    normalized, validation_errors = normalize_extraction(data)
                    if validation_errors or normalized is None:
                        q_file = write_validation_quarantine(
                            source_id=source_id,
                            source_path=str(f),
                            raw_payload=raw_payload,
                            errors=validation_errors,
                        )
                        logger.error("Invalid extraction quarantined: %s", q_file)
                        errors += 1
                        json_output_path.unlink(missing_ok=True)
                        continue

                    result = write_processed(
                        extracted=normalized,
                        source=source_type,
                        source_path=str(f),
                        source_id=source_id,
                    )
                    if result["success"]:
                        if result.get("duplicate"):
                            logger.info("Duplicate processed entry skipped: %s", result["id"])
                            skipped += 1
                        else:
                            logger.info("Written to processed: %s", result["id"])
                            dispatched += 1
                        new_hashes.append(content_hash)
                        remaining_budget -= 1
                    else:
                        logger.error("Write failed: %s", result.get("error"))
                        errors += 1
                    json_output_path.unlink(missing_ok=True)
                    logger.info("GA task completed: %s", task_name)
                except Exception as e:
                    logger.error("Failed to ingest from JSON: %s", e)
                    errors += 1
            else:
                errors += 1
                logger.error("GA task failed, no JSON: %s - %s", task_name,
                             result.get("error", result.get("stderr", "unknown")))

    # 更新游标
    if not dry_run:
        cursor["processed_hashes"] = list(set(processed_hashes) | set(new_hashes))
        cursor["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _save_cursor(cursor)

    result = {
        "total": total,
        "dispatched": dispatched,
        "skipped": skipped,
        "deferred": deferred,
        "errors": errors,
    }
    logger.info("Process complete: %s", result)
    if not dry_run:
        _update_process_metrics(result)
    return result
