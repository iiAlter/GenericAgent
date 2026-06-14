"""路径常量 — 全代码库单点引用。

架构说明：
- ~/.sidebrain/                    主库（GA 私有，独立于 Pi）
- Pi 和其他客户端通过 MCP pull 读取主库
- ~/.pi/agent/{memories,rules}/sidebrain/ 仅用于手动 sidebrain sync 导出
"""

import os
from pathlib import Path

# === 包根 ===
SIDEBRAIN_PKG_ROOT = Path(__file__).resolve().parents[1]

# === 主库（GA 私有数据主源）===
# 决策：2026-06-03 按 B 方案：~/.sidebrain/ 是 GA 独立主源。
#       用户原话："你也要自己有一份记忆，Pi 的记忆库是从你这边过去的"。
#       可被 SIDEBRAIN_HOME 环境变量覆盖。
SIDEBRAIN_HOME = Path(os.environ.get(
    "SIDEBRAIN_HOME",
    str(Path.home() / ".sidebrain"),
))

KNOWLEDGE = SIDEBRAIN_HOME / "knowledge"
PROCESSED = KNOWLEDGE / "processed"
RAW_PI = KNOWLEDGE / "raw" / "pi"
RAW_MEETINGS = KNOWLEDGE / "raw" / "meetings"
RAW_AD_HOC = KNOWLEDGE / "raw" / "ad_hoc"
RAW_GA = KNOWLEDGE / "raw" / "ga"
QUARANTINE = KNOWLEDGE / "quarantine"
STATE = SIDEBRAIN_HOME / "state"
LOGS = SIDEBRAIN_HOME / "logs"

# === GA 引用 ===
GA_ROOT = Path.home() / "serve" / "GenericAgent"

# === Pi 源 ===
PI_HOME = Path.home() / ".pi" / "agent"
PI_SESSIONS = PI_HOME / "sessions"
PI_MEMORIES_MIRROR = PI_HOME / "memories" / "sidebrain"   # 手动导出副本
PI_RULES_MIRROR = PI_HOME / "rules" / "sidebrain"

# === 源文件 dropbox ===
SOURCES_MEETINGS = SIDEBRAIN_PKG_ROOT / "sources" / "meetings"
SOURCES_AD_HOC = SIDEBRAIN_PKG_ROOT / "sources" / "ad_hoc"

# === 需要 mkdir 的目录 ===
_ALL_DIRS = [
    PROCESSED, RAW_PI, RAW_MEETINGS, RAW_AD_HOC, RAW_GA, QUARANTINE,
    STATE, LOGS,
]


def ensure_dirs() -> list[Path]:
    created: list[Path] = []
    for d in _ALL_DIRS:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(d)
    return created
