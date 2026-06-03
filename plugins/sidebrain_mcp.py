"""
Sidebrain MCP 插件 — 为 GA 注入 sidebrain 工具。

工作原理：
1. 在 tools_schema.json 末尾追加 4 个 sidebrain 工具
2. 在 GenericAgentHandler 上动态添加 do_sidebrain_* 方法
3. 方法内部通过 MCP 客户端调用 Node.js MCP server
"""

import json
import os
import sys
from pathlib import Path

# 加载 MCP 客户端
_script_dir = os.path.dirname(os.path.abspath(__file__))
_sidebrain_dir = os.path.join(os.path.dirname(_script_dir), 'sidebrain', 'sidebrain')
if _sidebrain_dir not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(_script_dir)))

# ============================================================
# 工具定义
# ============================================================

SIDEBRAIN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "sidebrain_search",
            "description": "搜索 sidebrain 知识库，查找之前讨论过的决策、架构、配置等信息。跨会话记忆查询。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "description": "最大返回条数", "default": 10},
                },
                "required": ["query"],
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sidebrain_get",
            "description": "获取 sidebrain 单条记忆的详细信息，包括决策详情、人物、行动项等完整内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic_id": {"type": "string", "description": "记忆 ID 或文件名关键词"},
                },
                "required": ["topic_id"],
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sidebrain_ingest",
            "description": "将信息存入 sidebrain 知识库。已分析过的内容用 summary/key_points 结构化模式，原始文本用 text 模式。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "原始文本内容（简单模式，不传 summary 时使用）"},
                    "source": {"type": "string", "description": "来源标识", "default": "ga"},
                    "summary": {"type": "string", "description": "摘要（结构化模式，传此字段时直接入库）"},
                    "key_points": {"type": "array", "items": {"type": "string"}, "description": "关键点列表"},
                    "action_items": {"type": "array", "items": {"type": "string"}, "description": "行动项列表"},
                    "decisions": {"type": "array", "items": {"type": "object", "properties": {"decision": {"type": "string"}, "reasoning": {"type": "string"}, "alternatives": {"type": "string"}}}, "description": "决策列表"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表"},
                    "confidence": {"type": "number", "description": "置信度 0-1"},
                },
                "anyOf": [{"required": ["text"]}, {"required": ["summary"]}],
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sidebrain_list_projects",
            "description": "列出 sidebrain 知识库中所有项目名称。",
            "parameters": {"type": "object", "properties": {}},
        }
    },
]


def _patch_tools_schema():
    """在 tools_schema.json 末尾追加 sidebrain 工具。"""
    schema_path = os.path.join(os.path.dirname(_script_dir), 'assets', 'tools_schema.json')
    if not os.path.exists(schema_path):
        return

    try:
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)

        # 检查是否已存在
        existing_names = {t.get('function', {}).get('name') for t in schema}
        new_tools = [t for t in SIDEBRAIN_TOOLS if t['function']['name'] not in existing_names]

        if new_tools:
            schema.extend(new_tools)
            with open(schema_path, 'w', encoding='utf-8') as f:
                json.dump(schema, f, ensure_ascii=False, indent=2)
            print(f"[sidebrain] 已注入 {len(new_tools)} 个工具到 tools_schema.json")
    except Exception as e:
        print(f"[sidebrain] 注入 tools_schema 失败: {e}")


def _patch_handler():
    """在 GenericAgentHandler 上动态添加 sidebrain 工具处理方法。"""
    from ga import GenericAgentHandler
    from agent_loop import StepOutcome

    # MCP 客户端（延迟导入，避免启动时加载）
    _mcp_client = None

    def _get_client():
        nonlocal _mcp_client
        if _mcp_client is None:
            from sidebrain.mcp_client import search, get_detail, list_projects, ingest, close
            _mcp_client = {
                "search": search,
                "get_detail": get_detail,
                "list_projects": list_projects,
                "ingest": ingest,
                "close": close,
            }
        return _mcp_client

    # --- sidebrain_search ---
    def do_sidebrain_search(self, args, response):
        client = _get_client()
        query = args.get("query", "")
        limit = int(args.get("limit", 10))
        try:
            result = client["search"](query, limit) or "未找到相关记忆。"
            return StepOutcome(f"[sidebrain_search] {result}", next_prompt="\n")
        except Exception as e:
            return StepOutcome(f"[sidebrain_search] 错误: {e}", next_prompt="\n")

    # --- sidebrain_get ---
    def do_sidebrain_get(self, args, response):
        client = _get_client()
        topic_id = args.get("topic_id", "")
        try:
            text = client["get_detail"](topic_id)
            result = text or "未找到。"
            return StepOutcome(f"[sidebrain_get] {result}", next_prompt="\n")
        except Exception as e:
            return StepOutcome(f"[sidebrain_get] 错误: {e}", next_prompt="\n")

    # --- sidebrain_ingest ---
    def do_sidebrain_ingest(self, args, response):
        from sidebrain.mcp_client import call_tool
        try:
            result = call_tool("sidebrain_ingest", args)
            resp_text = ""
            content = result.get("content", [])
            for c in content:
                if c.get("type") == "text":
                    resp_text = c.get("text", "")
            return StepOutcome(f"[sidebrain_ingest] ✅ {resp_text}", next_prompt="\n")
        except Exception as e:
            return StepOutcome(f"[sidebrain_ingest] 错误: {e}", next_prompt="\n")

    # --- sidebrain_list_projects ---
    def do_sidebrain_list_projects(self, args, response):
        client = _get_client()
        try:
            projects = client["list_projects"]()
            if projects:
                result = "可用项目:\n" + "\n".join(f"  - {p}" for p in projects)
            else:
                result = "知识库中暂无项目。"
            return StepOutcome(f"[sidebrain_list_projects] {result}", next_prompt="\n")
        except Exception as e:
            return StepOutcome(f"[sidebrain_list_projects] 错误: {e}", next_prompt="\n")

    # 注入 sidebrain 工具方法
    for name, func in [
        ("do_sidebrain_search", do_sidebrain_search),
        ("do_sidebrain_get", do_sidebrain_get),
        ("do_sidebrain_ingest", do_sidebrain_ingest),
        ("do_sidebrain_list_projects", do_sidebrain_list_projects),
    ]:
        if not hasattr(GenericAgentHandler, name):
            setattr(GenericAgentHandler, name, func)

    print(f"[sidebrain] 已注入 4 个工具方法到 GenericAgentHandler")

    # --- monkey-patch start_long_term_update ---
    # 在原有 prompt 末尾追加 sidebrain 保存指令
    _orig_start = GenericAgentHandler.do_start_long_term_update

    def _patched_start(self, args, response):
        gen = _orig_start(self, args, response)
        try:
            outcome = next(gen)
            # 在返回的 prompt 末尾追加 sidebrain 指令
            sidebrain_hint = (
                "\n\n### [Sidebrain] 提取完成后，用 `sidebrain_ingest` 将验证成功的信息"
                "同时保存到 sidebrain 知识库，以便跨会话回溯。"
                "把本次提取的关键事实、配置、决策整理成一段话，调用 sidebrain_ingest 存入。"
            )
            if isinstance(outcome, StepOutcome):
                outcome.next_prompt += sidebrain_hint
            yield outcome
            yield from gen
        except Exception:
            yield from gen

    GenericAgentHandler.do_start_long_term_update = _patched_start
    print(f"[sidebrain] 已 hook start_long_term_update → 自动同步到 sidebrain")


# ============================================================
# 插件入口（GA 启动时自动执行）
# ============================================================

_patched = False


def _ensure_patched():
    global _patched
    if not _patched:
        _patch_tools_schema()
        _patch_handler()
        _patched = True


# GA 的 plugins/hooks.discover_and_load() 会 import 此文件，
# 顶层代码在 import 时执行
_ensure_patched()
print("[sidebrain] MCP 插件已加载")
