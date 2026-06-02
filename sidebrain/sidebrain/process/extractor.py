"""LLM 提取器 — 从原始 Markdown 中提取结构化信息。

读取 ~/.sidebrain/knowledge/raw/ 下的文件，
调用 LLM 提取关键点/行动项/决策/人物/项目，
输出结构化 JSON 用于写入 processed/。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from sidebrain.config import load_config
from sidebrain.llm import quick_ask
from sidebrain.paths import SIDEBRAIN_PKG_ROOT

logger = logging.getLogger(__name__)


def _load_prompt(source_type: str) -> str:
    """加载对应的 prompt 模板。

    Args:
        source_type: pi_session / meeting / ad_hoc

    Returns:
        Prompt 模板内容。
    """
    cfg = load_config()
    prompts_dir = Path(cfg["llm"]["prompts_dir"])

    prompt_map = {
        "pi_session": "extract_pi.md",
        "meeting": "extract_meeting.md",
        "ad_hoc": "extract_ad_hoc.md",
    }

    filename = prompt_map.get(source_type, "extract_pi.md")
    prompt_path = prompts_dir / filename

    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    else:
        # 回退到内嵌默认 prompt
        return _default_prompt(source_type)


def _default_prompt(source_type: str) -> str:
    """内嵌默认 prompt（当文件不存在时）。"""
    return (
        f"从以下{source_type}记录中提取关键信息。"
        f"输出为严格的 JSON 格式，不要添加任何额外说明。\n\n"
        f"{{\n"
        f'  "summary": "...",\n'
        f'  "key_points": ["..."],\n'
        f'  "action_items": ["..."],\n'
        f'  "tags": ["..."],\n'
        f'  "confidence": 0.0\n'
        f"}}"
    )


def _parse_llm_response(raw: str) -> dict[str, Any] | None:
    """解析 LLM 的 JSON 响应。

    处理可能的 markdown 代码块包裹。
    """
    # 去掉可能的 ```json ... ``` 包裹
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # 找到代码块内容
        lines = cleaned.split("\n")
        code_lines = []
        in_code = False
        for line in lines:
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                code_lines.append(line)
        if code_lines:
            cleaned = "\n".join(code_lines)

    # 尝试解析 JSON
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 尝试查找 JSON 对象
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse LLM response as JSON")
    return None


def extract_from_markdown(
    markdown_text: str,
    source_type: str = "pi_session",
    model: str | None = None,
) -> dict[str, Any] | None:
    """从原始 Markdown 中提取结构化信息。

    Args:
        markdown_text: 原始 Markdown 文本。
        source_type: 来源类型（pi_session / meeting / ad_hoc）。
        model: LLM 模型名，默认使用配置中的 default_model。

    Returns:
        提取结果字典，或 None。
    """
    prompt = _load_prompt(source_type)

    # 构建完整的 prompt
    full_prompt = f"{prompt}\n\n---\n\n{markdown_text[:5000]}"

    if len(markdown_text) > 5000:
        logger.debug("Markdown truncated to 5000 chars for LLM extraction")

    try:
        response = quick_ask(full_prompt, cfg_name=model)
    except Exception as e:
        logger.error("LLM extraction failed: %s", e)
        return None

    if not response:
        logger.warning("LLM returned empty response")
        return None

    return _parse_llm_response(response)


def extract_file(
    file_path: str | Path,
    source_type: str = "pi_session",
) -> dict[str, Any] | None:
    """从原始 Markdown 文件中提取结构化信息。

    Args:
        file_path: 原始 .md 文件路径。
        source_type: 来源类型。

    Returns:
        提取结果字典，或 None。
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning("File not found: %s", path)
        return None

    text = path.read_text(encoding="utf-8")
    return extract_from_markdown(text, source_type=source_type)
