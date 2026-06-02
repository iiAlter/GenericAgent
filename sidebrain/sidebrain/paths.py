"""路径常量 — 全代码库单点引用。

架构说明：
- ~/.pi/sidebrain/                 主库（当前服务器唯一真相）
- ~/.pi/agent/memories/sidebrain/  Pi 本地副本（每次 session_start 同步）
- 其他 agent 各自同步
"""

import os
from pathlib import Path

# === 包根 ===
SIDEBRAIN_PKG_ROOT = Path(__file__).resolve().parents[1]

# === 主库（当前服务器，唯一真相）===
SIDEBRAIN_HOME = Path(os.environ.get(
    "SIDEBRAIN_HOME",
    str(Path.home() / ".pi" / "sidebrain"),
))

KNOWLEDGE = SIDEBRAIN_HOME / "knowledge"
PROCESSED = KNOWLEDGE / "processed"
RAW_PI = KNOWLEDGE / "raw" / "pi"
RAW_MEETINGS = KNOWLEDGE / "raw" / "meetings"
RAW_AD_HOC = KNOWLEDGE / "raw" / "ad_hoc"
QUARANTINE = KNOWLEDGE / "quarantine"
STATE = SIDEBRAIN_HOME / "state"
LOGS = SIDEBRAIN_HOME / "logs"

# === GA 引用 ===
GA_ROOT = Path.home() / "serve" / "GenericAgent"

# === Pi 源 ===
PI_HOME = Path.home() / ".pi" / "agent"
PI_SESSIONS = PI_HOME / "sessions"
PI_MEMORIES_MIRROR = PI_HOME / "memories" / "sidebrain"   # Pi 本地副本
PI_RULES_MIRROR = PI_HOME / "rules" / "sidebrain"

# === 源文件 dropbox ===
SOURCES_MEETINGS = SIDEBRAIN_PKG_ROOT / "sources" / "meetings"
SOURCES_AD_HOC = SIDEBRAIN_PKG_ROOT / "sources" / "ad_hoc"

# === 需要 mkdir 的目录 ===
_ALL_DIRS = [
    PROCESSED, RAW_PI, RAW_MEETINGS, RAW_AD_HOC, QUARANTINE,
    STATE, LOGS,
    PI_MEMORIES_MIRROR, PI_RULES_MIRROR,
]


def ensure_dirs() -> list[Path]:
    created: list[Path] = []
    for d in _ALL_DIRS:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(d)
    return created
