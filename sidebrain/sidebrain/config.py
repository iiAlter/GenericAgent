"""配置加载 — YAML 配置 + 默认值合并。"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any

import yaml

from sidebrain.paths import SIDEBRAIN_PKG_ROOT, GA_ROOT

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "client": "ga_llmcore",
        "default_model": "native_oai_config",
        "high_value_model": "mixin_config",
        "key_source": str(GA_ROOT / "mykey.py"),
        "timeout_sec": 60,
        "max_retries": 3,
        "prompts_dir": str(SIDEBRAIN_PKG_ROOT / "prompts"),
    },
    "ingest": {
        "pi": {
            "enabled": True,
            "session_glob": "**/*.jsonl",
            "batch_size": 10,
            "max_size_mb": 50,
        },
        "meetings": {
            "enabled": True,
            "glob": "**/*.{md,txt}",
            "batch_size": 10,
        },
    },
    "process": {
        "batch_size": 10,
        "max_concurrent_llm": 2,
        "single_file_token_limit": 20000,
        "daily_budget_usd": 1.0,
    },
    "sync": {
        "max_items": 50,
        "dry_run": False,
        "tags_filter": ["general", "rule", "preference", "sidebrain", "MCP", "架构方案", "目录结构", "项目规范"],
    },
    "mcp": {
        "host": "127.0.0.1",
        "port": 0,
        "transport": "stdio",
    },
    "daemon": {
        "interval_sec": 300,
        "log_rotate_mb": 10,
        "log_rotate_count": 5,
    },
}


def _find_config() -> Path | None:
    """查找配置文件的搜索路径（优先用户自定义）。"""
    candidates = [
        Path.cwd() / "sidebrain.config.yaml",
        Path.cwd() / "sidebrain.config.yml",
        SIDEBRAIN_PKG_ROOT / "config.yaml",
        SIDEBRAIN_PKG_ROOT / "config.yml",
        Path.home() / ".config" / "sidebrain" / "config.yaml",
    ]
    env_path = os.environ.get("SIDEBRAIN_CONFIG")
    if env_path:
        candidates.insert(0, Path(env_path))

    for p in candidates:
        if p.exists():
            logger.debug("Found config: %s", p)
            return p
    return None


def _merge_dict(base: dict, override: dict) -> dict:
    """深度合并字典，override 覆盖 base。"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _load_env_overrides(cfg: dict) -> dict:
    """环境变量覆盖：SIDEBRAIN_LLM_MODEL, SIDEBRAIN_LLM_HIGH_MODEL 等。"""
    overrides = {
        ("llm", "default_model"): os.environ.get("SIDEBRAIN_LLM_MODEL"),
        ("llm", "high_value_model"): os.environ.get("SIDEBRAIN_LLM_HIGH_MODEL"),
        ("llm", "timeout_sec"): os.environ.get("SIDEBRAIN_LLM_TIMEOUT"),
        ("mcp", "transport"): os.environ.get("SIDEBRAIN_MCP_TRANSPORT"),
        ("daemon", "interval_sec"): os.environ.get("SIDEBRAIN_DAEMON_INTERVAL"),
    }
    for keys, value in overrides.items():
        if value is None:
            continue
        target = cfg
        for k in keys[:-1]:
            target = target.setdefault(k, {})
        # 数值类型转换
        key = keys[-1]
        if key.endswith("_sec") or key == "interval_sec":
            try:
                target[key] = int(value)
            except ValueError:
                logger.warning("Invalid int for %s: %s", ".".join(keys), value)
        else:
            target[key] = value
    return cfg


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """加载配置，合并默认值 + 文件覆盖 + 环境变量覆盖。

    Args:
        path: 可选，显式指定配置文件路径。

    Returns:
        完整配置字典。
    """
    cfg = _DEFAULT_CONFIG.copy()

    if path is None:
        found = _find_config()
        if found:
            path = found

    if path:
        path = Path(path)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                file_cfg = yaml.safe_load(f) or {}
            cfg = _merge_dict(cfg, file_cfg)
            logger.info("Loaded config from %s", path)
        else:
            logger.warning("Config file not found: %s", path)

    cfg = _load_env_overrides(cfg)

    # 解析 prompts_dir 为绝对路径
    prompts_dir = cfg["llm"]["prompts_dir"]
    if not Path(prompts_dir).is_absolute():
        cfg["llm"]["prompts_dir"] = str(SIDEBRAIN_PKG_ROOT / prompts_dir)

    return cfg


def print_config_safe(cfg: dict[str, Any]) -> None:
    """安全打印配置（脱敏 key 相关字段）。"""
    safe = cfg.copy()
    llm = safe.get("llm", {})
    if "key_source" in llm:
        llm["key_source"] = "*** (hidden)"
    if "api_key" in llm:
        llm["api_key"] = "*** (hidden)"
    import json
    print(json.dumps(safe, indent=2, ensure_ascii=False))
