"""sidebrain — Pi 的侧载大脑。

从 Pi 会话、会议纪要、用户临时输入中自动提取知识，
沉淀到本地知识库，并通过 MCP 协议供任意 agent 调用。
"""

import logging
import os
from pathlib import Path

__version__ = "0.1.0"


def _setup_logging() -> None:
    """初始化全局 logging：同时输出到 stderr 和按日滚动的日志文件。"""
    from sidebrain.paths import LOGS

    LOGS.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger("sidebrain")
    if root_logger.handlers:
        return

    root_logger.setLevel(logging.DEBUG if os.environ.get("SIDEBRAIN_DEBUG") else logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # stderr handler（仅 WARNING+）
    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    root_logger.addHandler(sh)

    # 按日滚动的文件 handler
    from logging.handlers import TimedRotatingFileHandler
    fh = TimedRotatingFileHandler(
        LOGS / "sidebrain.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    fh.suffix = "%Y-%m-%d"
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root_logger.addHandler(fh)

    root_logger.debug("Logging initialized")


_setup_logging()
