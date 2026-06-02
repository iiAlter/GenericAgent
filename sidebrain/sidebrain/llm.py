"""LLM client wrapper — 包装 GenericAgent llmcore.py。

复用 GA 的 LLM 通道，不复制 API key。
支持快速问答和流式调用。
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import Any

from sidebrain.paths import GA_ROOT, GA_MYKEY

logger = logging.getLogger(__name__)

# 懒加载：只在首次调用时 import llmcore
_llmcore = None
_mykeys = None


def _ensure_llmcore():
    """确保 llmcore 可导入。"""
    global _llmcore, _mykeys

    if _llmcore is not None:
        return _llmcore

    # GA 在 sys.path 中吗？
    ga_root_str = str(GA_ROOT)
    if ga_root_str not in sys.path:
        sys.path.insert(0, ga_root_str)

    try:
        _llmcore = importlib.import_module("llmcore")
        _mykeys = _llmcore.reload_mykeys()[0]
        logger.info("llmcore 已加载，key: %s", _llmcore._mykey_path)
    except Exception as e:
        logger.error("加载 llmcore 失败: %s", e)
        raise

    return _llmcore


def resolve_session(cfg_name: str | None = None) -> Any:
    """解析 LLM session 对象。

    Args:
        cfg_name: 配置名（如 'deepseek/deepseek-v4-flash'）。
                  默认使用配置文件中的 default_model。

    Returns:
        LLM session 对象。
    """
    lc = _ensure_llmcore()
    if cfg_name is None:
        from sidebrain.config import load_config

        cfg = load_config()
        cfg_name = cfg["llm"]["default_model"]
    return lc.resolve_session(cfg_name)


def quick_ask(prompt: str, cfg_name: str | None = None) -> str:
    """简单 QA：发送 prompt，返回完整响应字符串。

    Args:
        prompt: 要发送的提示词。
        cfg_name: 模型配置名。

    Returns:
        LLM 响应的文本。
    """
    lc = _ensure_llmcore()
    if cfg_name is None:
        from sidebrain.config import load_config

        cfg = load_config()
        cfg_name = cfg["llm"]["default_model"]
    return lc.fast_ask(prompt, cfg_name)


def get_available_models() -> list[str]:
    """列出 mykey 中可用的模型配置名。"""
    _ensure_llmcore()
    keys = _llmcore.reload_mykeys()[0]
    return [k for k in keys if not k.startswith("_")]
