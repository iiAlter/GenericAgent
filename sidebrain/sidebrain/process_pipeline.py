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
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from sidebrain.paths import GA_ROOT, PROCESSED, RAW_PI, RAW_MEETINGS, RAW_AD_HOC, STATE
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
    import os as _os
    _os.fsync(tmp.fileno() if hasattr(tmp, "fileno") else _os.open(tmp, _os.O_RDONLY))
    tmp.replace(CURSOR_FILE)


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


def process_all(dry_run: bool = False) -> dict[str, Any]:
    """扫描 raw/ 下未处理数据，逐个创建 GA 任务。

    Args:
        dry_run: 仅模拟，不实际调用 GA。

    Returns:
        处理统计。
    """
    cursor = _load_cursor()
    processed_hashes = set(cursor.get("processed_hashes", []))

    raw_dirs = [
        (RAW_PI, "pi_session"),
        (RAW_MEETINGS, "meeting"),
        (RAW_AD_HOC, "ad_hoc"),
    ]

    total = 0
    dispatched = 0
    skipped = 0
    errors = 0
    new_hashes: list[str] = []

    for raw_dir, source_type in raw_dirs:
        if not raw_dir.exists():
            continue

        # 所有 raw 目录都扫 .md，pi 额外扫 .jsonl
        files = sorted(raw_dir.glob("*.md"))
        if source_type == "pi_session":
            files += sorted(raw_dir.glob("*.jsonl"))
        logger.info("Found %d raw files in %s", len(files), raw_dir.name)

        for f in files:
            total += 1
            source_id = f.stem

            # 检查是否已被处理
            raw_bytes = f.read_bytes()
            content_hash = __import__("hashlib").sha256(raw_bytes).hexdigest()
            if content_hash in processed_hashes:
                skipped += 1
                continue

            if dry_run:
                logger.info("[DRY RUN] Would dispatch to GA: %s", source_id)
                new_hashes.append(content_hash)
                dispatched += 1
                continue

            # 构建 prompt 给 GA — 输出 JSON 文件，Python 端负责 ingest
            json_output_path = GA_ROOT / "temp" / f"_{source_id}_extracted.json"
            relative_path = str(f.relative_to(f.parents[2])) if len(f.parents) > 2 else f.name
            prompt = (
                f"请分析以下 {source_type} 数据，提取关键信息后写入 JSON 文件。\n\n"
                f"步骤：\n"
                f"1. 用 file_read 读取: {f}\n"
                f"2. 分析内容，提取摘要、关键点、行动项、决策\n"
                f"3. 用 file_write 将结果写入 JSON 文件: {json_output_path}\n\n"
                f"JSON 格式：\n"
                f"{{\n"
                f'  "summary": "一句话摘要",\n'
                f'  "key_points": ["关键点1", "关键点2"],\n'
                f'  "action_items": ["行动项1", "行动项2"],\n'
                f'  "decisions": ["决策1", "决策2"],\n'
                f'  "tags": ["标签1", "标签2"]\n'
                f"}}\n\n"
                f"数据路径: {f}\n"
                f"来源类型: {source_type}\n"
                f"来源标识: {source_id}\n"
                f"JSON 输出路径: {json_output_path}\n"
            )

            task_name = f"sidebrain_{source_type}_{source_id[:20]}"
            result = _call_ga(task_name, prompt)

            # 检查 JSON 输出（GA 超时时也可能已生成）
            if json_output_path.exists():
                try:
                    data = json.loads(json_output_path.read_text(encoding="utf-8"))
                    result = write_processed(
                        extracted=data,
                        source=source_type,
                        source_path=str(f),
                        source_id=source_id,
                    )
                    if result["success"]:
                        logger.info("Written to processed: %s", result["id"])
                        dispatched += 1
                        new_hashes.append(content_hash)
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
        "errors": errors,
    }
    logger.info("Process complete: %s", result)
    return result
