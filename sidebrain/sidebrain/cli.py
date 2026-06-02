"""CLI 入口 — 注册所有子命令并分派。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import NoReturn

from sidebrain import __version__
from sidebrain.config import load_config, print_config_safe
from sidebrain.paths import ensure_dirs, SOURCES_MEETINGS, SOURCES_AD_HOC, SIDEBRAIN_HOME

logger = logging.getLogger(__name__)


def cmd_init(args: argparse.Namespace) -> None:
    """初始化 sidebrain 目录树和 sources/。"""
    created = ensure_dirs()

    # 确保 sources 目录存在
    SOURCES_MEETINGS.mkdir(parents=True, exist_ok=True)
    SOURCES_AD_HOC.mkdir(parents=True, exist_ok=True)

    # 写入 sources/meetings/README.md
    meetings_readme = SOURCES_MEETINGS / "README.md"
    if not meetings_readme.exists():
        meetings_readme.write_text(
            "# 会议纪要目录\n\n"
            "把你需要 sidebrain 处理的会议纪要（.md / .txt）放在这里。\n"
            "sidebrain 会自动扫描并提取行动项和关键信息。\n"
        )

    # 写入 sources/ad_hoc/.gitkeep
    gitkeep = SOURCES_AD_HOC / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("")

    print(f"✓ Sidebrain 数据目录已初始化: {created}")
    print(f"  数据根: {SIDEBRAIN_HOME}")
    print(f"  会议来源: {SOURCES_MEETINGS}")
    print(f"  临时来源: {SOURCES_AD_HOC}")


def cmd_config(args: argparse.Namespace) -> None:
    """打印当前生效配置（脱敏）。"""
    cfg = load_config(args.config)
    print_config_safe(cfg)


def cmd_doctor(args: argparse.Namespace) -> None:
    """健康自检：依赖/路径/权限/LLM 联通。"""
    cfg = load_config(args.config)
    all_ok = True

    print("=== Sidebrain Doctor ===")
    print()

    # 1. 依赖检查
    print("[1/6] 依赖检查...")
    deps = [
        ("pyyaml", "yaml"),
        ("mcp", "mcp"),
    ]
    for name, mod_name in deps:
        try:
            __import__(mod_name)
            print(f"  ✓ {name}")
        except ImportError:
            print(f"  ✗ {name} — 未安装")
            all_ok = False

    # 2. 路径检查
    print("[2/6] 路径检查...")
    try:
        created = ensure_dirs()
        if created:
            print(f"  ✗ 部分目录不存在，已创建: {created}")
        else:
            print(f"  ✓ 所有数据目录已存在")
    except Exception as e:
        print(f"  ✗ 目录创建失败: {e}")
        all_ok = False

    # 3. GA llmcore 可导入
    print("[3/6] GA llmcore 联通...")
    try:
        import importlib  # noqa: F811

        spec = importlib.util.find_spec("llmcore")
        if spec is None:
            print("  ✗ llmcore 未在 sys.path 中")
            all_ok = False
        else:
            print(f"  ✓ llmcore 位于 {spec.origin}")
    except Exception as e:
        print(f"  ✗ llmcore 导入失败: {e}")
        all_ok = False

    # 4. mykey 可加载
    print("[4/6] GA mykey 检查...")
    ga_mykey = Path.home() / "serve" / "GenericAgent" / "mykey.py"
    if ga_mykey.exists():
        print(f"  ✓ mykey 存在: {ga_mykey}")
    else:
        print(f"  ✗ mykey 不存在: {ga_mykey}")
        all_ok = False

    # 5. LLM ping
    print("[5/6] LLM 快速测试...")
    try:
        from sidebrain.llm import quick_ask

        result = quick_ask('回复"OK"即可', cfg_name=cfg["llm"]["default_model"])
        if result:
            print(f"  ✓ LLM 响应正常: {result[:80]}...")
        else:
            print("  ✗ LLM 返回空")
            all_ok = False
    except Exception as e:
        print(f"  ✗ LLM 测试失败: {e}")
        all_ok = False

    # 6. MCP 可用
    print("[6/6] MCP SDK 检查...")
    try:
        import mcp  # noqa: F811

        print(f"  ✓ MCP SDK {getattr(mcp, '__version__', '?')}")
    except Exception as e:
        print(f"  ✗ MCP SDK 错误: {e}")
        all_ok = False

    print()
    if all_ok:
        print("✓ 全部正常")
    else:
        print("✗ 部分检查失败，请修复后重试")

    sys.exit(0 if all_ok else 1)


def cmd_status(args: argparse.Namespace) -> None:
    """状态汇总：计数/最后运行/quarantine/下一批。"""
    from sidebrain.paths import RAW_PI, RAW_MEETINGS, RAW_AD_HOC, PROCESSED, QUARANTINE, STATE

    cfg = load_config(args.config)

    pi_files = list(RAW_PI.glob("*.md")) if RAW_PI.exists() else []
    meeting_files = list(RAW_MEETINGS.glob("*.md")) if RAW_MEETINGS.exists() else []
    ad_hoc_files = list(RAW_AD_HOC.glob("*.md")) if RAW_AD_HOC.exists() else []
    processed = list(PROCESSED.rglob("*.md")) if PROCESSED.exists() else []
    quarantined = list(QUARANTINE.glob("*.md")) if QUARANTINE.exists() else []

    # 游标
    pi_cursor = STATE / "pi_cursor.json"
    meeting_cursor = STATE / "meeting_cursor.json"

    import json

    pi_cursor_data = {}
    meeting_cursor_data = {}
    if pi_cursor.exists():
        pi_cursor_data = json.loads(pi_cursor.read_text())
    if meeting_cursor.exists():
        meeting_cursor_data = json.loads(meeting_cursor.read_text())

    last_pi = pi_cursor_data.get("last_mtime_ns", "N/A")
    last_meeting = meeting_cursor_data.get("last_mtime_ns", "N/A")

    # metrics
    metrics_file = STATE / "metrics.json"
    metrics = {}
    if metrics_file.exists():
        metrics = json.loads(metrics_file.read_text())

    print("=== Sidebrain Status ===")
    print()
    print(f"  Raw PI sessions:    {len(pi_files)}")
    print(f"  Raw meetings:       {len(meeting_files)}")
    print(f"  Raw ad-hoc:         {len(ad_hoc_files)}")
    print(f"  Processed entries:  {len(processed)}")
    print(f"  Quarantined:        {len(quarantined)}")
    print(f"  Last PI cursor:     {last_pi}")
    print(f"  Last meeting cursor: {last_meeting}")
    print()
    if metrics:
        print("  Metrics:")
        for k, v in metrics.items():
            print(f"    {k}: {v}")


def _update_metrics(ingest_result: dict) -> None:
    """更新 metrics 文件。"""
    import json
    from sidebrain.paths import STATE

    metrics_file = STATE / "metrics.json"
    metrics = {}
    if metrics_file.exists():
        try:
            metrics = json.loads(metrics_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    metrics["ingested_pi"] = metrics.get("ingested_pi", 0) + ingest_result.get("ingested", 0)
    if ingest_result.get("ingested_pi") is not None:
        metrics["ingested_pi"] = ingest_result["ingested_pi"]
    if ingest_result.get("ingested_meetings") is not None:
        metrics["ingested_meetings"] = ingest_result["ingested_meetings"]
    metrics["last_run"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime())

    import os
    tmp = metrics_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(metrics, indent=2))
    tmp.replace(metrics_file)


def cmd_ingest_pi(args: argparse.Namespace) -> None:
    """摄入 Pi 会话。"""
    cfg = load_config(args.config)
    from sidebrain.ingest.pi_watcher import ingest_pi_sessions

    result = ingest_pi_sessions(
        batch_size=args.batch_size,
        max_size_mb=cfg["ingest"]["pi"]["max_size_mb"],
    )
    print(f"Ingested: {result['ingested']}, Skipped: {result['skipped']}, Errors: {result['errors']}")
    _update_metrics(result)


def cmd_ingest_meetings(args: argparse.Namespace) -> None:
    """摄入会议纪要。"""
    cfg = load_config(args.config)
    from sidebrain.ingest.meeting_watcher import ingest_meetings

    result = ingest_meetings(batch_size=cfg["ingest"]["meetings"]["batch_size"])
    print(f"Ingested: {result['ingested']}, Errors: {result['errors']}")
    _update_metrics(result)


def cmd_ingest_text(args: argparse.Namespace) -> None:
    """摄入文本（stdin 或参数）。"""
    from sidebrain.ingest.ad_hoc import ingest_text, ingest_stdin

    if args.text:
        result = ingest_text(args.text)
    else:
        result = ingest_stdin()

    print(f"Ingested: {result['ingested']}, ID: {result.get('id', 'N/A')}")


def cmd_process(args: argparse.Namespace) -> None:
    """处理 raw → processed。"""
    from sidebrain.process_pipeline import process_all

    result = process_all(dry_run=args.dry_run)
    print(f"Total: {result['total']}, Extracted: {result['extracted']}, "
          f"Written: {result['written']}, Skipped dup: {result['skipped_dup']}, "
          f"Errors: {result['errors']}")


def cmd_sync(args: argparse.Namespace) -> None:
    """同步 processed → Pi 镜像。"""
    cfg = load_config(args.config)
    from sidebrain.sync.pi_mirror import sync_to_pi

    result = sync_to_pi(
        tags_filter=cfg["sync"]["tags_filter"],
        max_items=args.max_items,
        dry_run=args.dry_run,
    )
    print(f"Total: {result['total']}, Synced: {result['synced']}, "
          f"Skipped: {result['skipped']}, Errors: {result['errors']}")


def cmd_mcp_serve(args: argparse.Namespace) -> None:
    """启动 MCP server（stdio）。"""
    from sidebrain.mcp.server import serve_stdio

    serve_stdio()


def cmd_mcp_install(args: argparse.Namespace) -> None:
    """注册 sidebrain MCP server 到 Pi。"""
    from sidebrain.mcp.install import install

    result = install()
    if result["success"]:
        print(f"✓ MCP server installed")
        if result.get("backup"):
            print(f"  Backup: {result['backup']}")
        if result.get("changed"):
            print(f"  Config updated")
        else:
            print(f"  Already up to date")
    else:
        print(f"✗ Install failed: {result.get('error')}")


def cmd_mcp_uninstall(args: argparse.Namespace) -> None:
    """从 Pi 移除 sidebrain MCP server。"""
    from sidebrain.mcp.install import uninstall

    result = uninstall()
    if result["success"]:
        print(f"✓ MCP server uninstalled")
        if result.get("backup"):
            print(f"  Backup: {result['backup']}")
    else:
        print(f"✗ Uninstall failed: {result.get('error')}")


def cmd_mcp_status(args: argparse.Namespace) -> None:
    """检查 MCP server 安装状态。"""
    from sidebrain.mcp.install import status

    st = status()
    if st["installed"]:
        print(f"✓ MCP server is installed")
        cfg = st.get("config", {})
        print(f"  Command: {cfg.get('command', '?')} {' '.join(cfg.get('args', []))}")
    else:
        print(f"✗ MCP server is not installed")


def cmd_daemon_start(args: argparse.Namespace) -> None:
    """启动守护进程。"""
    from sidebrain.daemon import start_daemon

    start_daemon(interval_sec=args.interval)


def cmd_daemon_stop(args: argparse.Namespace) -> None:
    """停止守护进程。"""
    from sidebrain.daemon import stop_daemon

    if stop_daemon():
        print("Stop signal sent")
    else:
        print("Daemon not running")


def cmd_daemon_status(args: argparse.Namespace) -> None:
    """检查守护进程状态。"""
    from sidebrain.daemon import daemon_status

    st = daemon_status()
    if st["running"]:
        print(f"Daemon is running (PID: {st['pid']})")
    else:
        print("Daemon is not running")


def cmd_health(args: argparse.Namespace) -> None:
    """运行健康检查。"""
    from sidebrain.health import print_health_report

    print(print_health_report())


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="sidebrain",
        description=f"Sidebrain v{__version__} — Pi 的侧载大脑",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default=None,
        help="配置文件路径（默认自动查找）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"sidebrain v{__version__}",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细日志输出",
    )

    sub = parser.add_subparsers(title="子命令", dest="command")

    # init
    p_init = sub.add_parser("init", help="初始化目录树和 sources/")
    p_init.set_defaults(func=cmd_init)

    # config
    p_config = sub.add_parser("config", help="打印当前配置（脱敏）")
    p_config.set_defaults(func=cmd_config)

    # doctor
    p_doctor = sub.add_parser("doctor", help="健康自检")
    p_doctor.set_defaults(func=cmd_doctor)

    # status
    p_status = sub.add_parser("status", help="状态汇总")
    p_status.set_defaults(func=cmd_status)

    # ingest
    p_ingest = sub.add_parser("ingest", help="摄入数据（pi/meetings/ad_hoc）")
    p_ingest_sub = p_ingest.add_subparsers(dest="ingest_source")

    p_ingest_pi = p_ingest_sub.add_parser("pi", help="摄入 Pi 会话")
    p_ingest_pi.add_argument("--batch-size", type=int, default=10, help="单次处理数量")
    p_ingest_pi.set_defaults(func=cmd_ingest_pi)

    p_ingest_meetings = p_ingest_sub.add_parser("meetings", help="摄入会议纪要")
    p_ingest_meetings.set_defaults(func=cmd_ingest_meetings)

    p_ingest_text = p_ingest_sub.add_parser("text", help="摄入文本（stdin 或参数）")
    p_ingest_text.add_argument("text", nargs="?", help="要摄入的文本（留空则读 stdin）")
    p_ingest_text.set_defaults(func=cmd_ingest_text)

    # process
    p_process = sub.add_parser("process", help="处理 raw → processed")
    p_process.add_argument("--dry-run", action="store_true", help="仅模拟预览")
    p_process.set_defaults(func=cmd_process)

    # sync
    p_sync = sub.add_parser("sync", help="同步到 Pi 镜像")
    p_sync.add_argument("--dry-run", action="store_true", help="仅模拟预览")
    p_sync.add_argument("--max-items", type=int, default=50, help="最大同步条数")
    p_sync.set_defaults(func=cmd_sync)

    # mcp
    p_mcp = sub.add_parser("mcp", help="MCP server 控制")
    p_mcp_sub = p_mcp.add_subparsers(dest="mcp_command")

    p_mcp_serve = p_mcp_sub.add_parser("serve", help="启动 MCP server（stdio）")
    p_mcp_serve.set_defaults(func=cmd_mcp_serve)

    p_mcp_install = p_mcp_sub.add_parser("install", help="注册到 Pi settings.json")
    p_mcp_install.set_defaults(func=cmd_mcp_install)

    p_mcp_uninstall = p_mcp_sub.add_parser("uninstall", help="从 Pi 中移除")
    p_mcp_uninstall.set_defaults(func=cmd_mcp_uninstall)

    p_mcp_status = p_mcp_sub.add_parser("status", help="检查安装状态")
    p_mcp_status.set_defaults(func=cmd_mcp_status)

    # daemon
    p_daemon = sub.add_parser("daemon", help="守护进程控制")
    p_daemon_sub = p_daemon.add_subparsers(dest="daemon_command")

    p_daemon_start = p_daemon_sub.add_parser("start", help="启动守护进程")
    p_daemon_start.add_argument("--interval", type=int, default=300, help="轮询间隔（秒）")
    p_daemon_start.set_defaults(func=cmd_daemon_start)

    p_daemon_stop = p_daemon_sub.add_parser("stop", help="停止守护进程")
    p_daemon_stop.set_defaults(func=cmd_daemon_stop)

    p_daemon_status = p_daemon_sub.add_parser("status", help="查看守护进程状态")
    p_daemon_status.set_defaults(func=cmd_daemon_status)

    # health
    p_health = sub.add_parser("health", help="运行健康检查")
    p_health.set_defaults(func=cmd_health)

    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI 主入口。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    # 日志配置
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.command is None:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
