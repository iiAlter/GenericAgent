"""处理 pipeline — 扫描 raw/ 下新数据，通知 GA 处理。

不再自己调 LLM 抽取，改为创建 GA 任务：
1. 扫描 raw/ 下所有未处理的文件
2. 对每个文件创建 GA task，通知 GA 去分析
3. GA 读取文件 → LLM 分析 → sidebrain_ingest 存回
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
        result = subprocess.run(
            cmd,
            cwd=str(GA_ROOT),
            capture_output=True,
            text=True,
            timeout=600,  # 10 分钟超时
        )

        # 读取输出
        output_files = sorted(task_dir.glob("output*.txt"))
        outputs = []
        for f in output_files:
            try:
                outputs.append(f.read_text(encoding="utf-8"))
            except Exception:
                pass

        return {
            "success": result.returncode == 0,
            "output": "\n".join(outputs) if outputs else result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "GA timeout (10min)"}
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

        files = sorted(raw_dir.glob("*.md"))
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

            # 构建 prompt 给 GA
            relative_path = str(f.relative_to(f.parents[2])) if len(f.parents) > 2 else f.name
            prompt = (
                f"请处理以下 {source_type} 数据：\n\n"
                f"步骤：\n"
                f"1. 先用 file_read 读取文件: {f}\n"
                f"2. 分析内容，提取摘要（summary）、关键点（key_points）、行动项（action_items）、决策（decisions）\n"
                f"3. 调用 sidebrain_ingest 工具（直接调用，不是命令行！），传入 text 参数包含提取的结构化信息\n"
                f"   - source 参数设为: {source_type}\n"
                f"   - 格式示例: sidebrain_ingest(text=\"摘要: ...\\n关键点: ...\\n行动项: ...\", source=\"{source_type}\")\n\n"
                f"数据路径: {f}\n"
                f"来源类型: {source_type}\n"
                f"来源标识: {source_id}\n"
            )

            task_name = f"sidebrain_{source_type}_{source_id[:20]}"
            result = _call_ga(task_name, prompt)

            if result["success"]:
                dispatched += 1
                new_hashes.append(content_hash)
                logger.info("GA task completed: %s", task_name)
            else:
                errors += 1
                logger.error("GA task failed: %s - %s", task_name,
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
