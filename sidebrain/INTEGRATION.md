# Sidebrain 第三方 Agent 接入手册

让任何支持 MCP 协议的 Agent（Claude Desktop、Cursor、自定义 Agent 等）接入 Sidebrain，
实现会话自动备份、跨会话记忆搜索、有价值信息自动沉淀。

## 1. 接入地址

```
https://mcp.yhao.ccwu.cc/sidebrain
```

所有 Agent 统一通过此 HTTP MCP 端点连接，无需本地部署。

## 2. 可用工具（6 个）

| 工具 | 说明 | 何时调用 |
|------|------|----------|
| `sidebrain_search` | 搜索历史决策/架构/配置 | 做决定前、用户提到「之前」「上次」 |
| `sidebrain_get` | 获取单条记忆详情 | 查看搜索结果的具体内容 |
| `sidebrain_ingest` | 摄入会话/决策到知识库 | 对话结束时自动调用 |
| `sidebrain_scan` | 触发服务端 GA 处理 raw → processed | 上传完会话后 |
| `sidebrain_list_projects` | 列出所有项目 | 浏览知识库内容 |
| `sidebrain_sync_pull` | 从服务器拉取全部数据到本地 | 首次初始化 |

## 3. 快速开始

### 3.1 Claude Desktop

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "sidebrain": {
      "url": "https://mcp.yhao.ccwu.cc/sidebrain"
    }
  }
}
```

**系统提示词**（在 Claude Desktop 设置中添加）：

```
你可以使用 sidebrain_* 工具读写跨会话持久记忆。
- 每次对话结束时，主动 sidebrain_ingest 有价值信息：
  架构决策、bug 修复方案、配置变更、用户偏好、重要结论。
  使用 source="claude", session_id="claude/<日期>-<项目>"。
- 做技术决策前，先 sidebrain_search 检查历史决策。
- 不要记录琐碎操作（ls/pwd/git status 等）。
```

### 3.2 Cursor

在 Cursor 设置 → MCP Servers 中添加：

```json
{
  "mcpServers": {
    "sidebrain": {
      "url": "https://mcp.yhao.ccwu.cc/sidebrain"
    }
  }
}
```

Cursor 的 Rules 文件中添加：

```
Use sidebrain_search before making architectural decisions.
After each session, proactively sidebrain_ingest key decisions and fixes
with source="cursor", session_id="cursor/<project>-<date>".
```

### 3.3 自定义 Agent（代码接入）

```python
import requests
import json
from datetime import datetime

SIDEBRAIN_URL = "https://mcp.yhao.ccwu.cc/sidebrain"

def sidebrain_call(tool_name: str, arguments: dict) -> dict:
    """通用 MCP 工具调用。"""
    resp = requests.post(SIDEBRAIN_URL, json={
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments}
    }, timeout=30)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Sidebrain error: {data['error']}")
    content = data.get("result", {}).get("content", [])
    return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")


# === 搜索历史记忆 ===
def sidebrain_search(query: str, limit: int = 10) -> str:
    return sidebrain_call("sidebrain_search", {"query": query, "limit": limit})


# === 摄入会话到待处理区 ===
def sidebrain_backup_session(session_text: str, agent: str, session_id: str):
    """将会话全文上传到 raw/<agent>/ 目录，等待 GA 分析。

    Args:
        session_text: 完整会话文本
        agent: agent 标识（如 "claude", "cursor", "my-agent"）
        session_id: 会话标识（如 "claude/2026-06-03-project-x"）
    """
    return sidebrain_call("sidebrain_ingest", {
        "text": session_text,
        "source": agent,
        "session_id": session_id,
    })


# === 摄入结构化决策 ===
def sidebrain_save_decision(summary: str, key_points: list, tags: list,
                            source: str = "my-agent"):
    """直接保存结构化决策到知识库。"""
    return sidebrain_call("sidebrain_ingest", {
        "summary": summary,
        "key_points": key_points,
        "tags": tags,
        "source": source,
    })


# === 触发服务端处理 ===
def sidebrain_trigger_process():
    """通知服务端启动 GA 分析 raw/ 中的新数据。"""
    return sidebrain_call("sidebrain_scan", {})


# === 完整会话结束流程 ===
def on_session_end(conversation_history: list, agent_name: str,
                   project: str = "general"):
    """会话结束时调用：备份全文 + 触发处理。"""
    session_id = f"{agent_name}/{datetime.now().strftime('%Y-%m-%d')}-{project}"
    session_text = json.dumps(conversation_history, ensure_ascii=False)

    # 1. 上传全文到 raw/<agent>/
    sidebrain_backup_session(session_text, agent_name, session_id)

    # 2. 触发 GA 处理
    sidebrain_trigger_process()

    print(f"会话已备份: {session_id}")
```

### 3.4 TypeScript/Node.js Agent

```typescript
const SIDEBRAIN_URL = "https://mcp.yhao.ccwu.cc/sidebrain";

async function sidebrainCall(tool: string, args: Record<string, any>) {
  const resp = await fetch(SIDEBRAIN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0", id: "1",
      method: "tools/call",
      params: { name: tool, arguments: args },
    }),
  });
  const data = await resp.json();
  if (data.error) throw new Error(`Sidebrain: ${data.error.message}`);
  return data.result.content
    .filter((c: any) => c.type === "text")
    .map((c: any) => c.text)
    .join("\n");
}

// 会话结束时
async function backupSession(messages: any[], agent: string, project: string) {
  const sessionId = `${agent}/${new Date().toISOString().slice(0, 10)}-${project}`;
  const text = JSON.stringify(messages);
  await sidebrainCall("sidebrain_ingest", {
    text, source: agent, session_id: sessionId,
  });
  await sidebrainCall("sidebrain_scan", {});
}
```

## 4. source 与 session_id 约定

`source` 和 `session_id` 决定数据在 `raw/` 中的存储位置：

| source | session_id 示例 | 存储路径 |
|--------|----------------|----------|
| `pi-session:N100` | `N100/--home-yuholy--/session-123` | `raw/pi/N100/--home-yuholy--/session-123.md` |
| `ga` | `ga/2026-06-03-feishu-chat` | `raw/ga/ga/2026-06-03-feishu-chat.md` |
| `claude` | `claude/2026-06-03-myproject` | `raw/claude/claude/2026-06-03-myproject.md` |
| `cursor` | `cursor/2026-06-03-work` | `raw/cursor/cursor/2026-06-03-work.md` |
| `my-custom-agent` | `my-custom-agent/session-001` | `raw/my_custom_agent/my-custom-agent/session-001.md` |

**规则**：
- `source` 以 `pi-session` 开头 → `raw/pi/<hostname>/`
- `source` 以 `ga` 开头 → `raw/ga/`
- `source` 以 `meeting` 开头 → `raw/meetings/`
- 其他 → `raw/<source名>/`（自动清理特殊字符）
- `session_id` 中的 `/` 会被解析为子目录

## 5. 完整工作流

```
┌──────────────────────────────────────────────────────┐
│              第三方 Agent 会话中                        │
│                                                      │
│  1. 做决策前：sidebrain_search("架构方案")             │
│     → 检查历史决策，避免冲突                           │
│                                                      │
│  2. 对话中：遇到重要信息时                             │
│     → sidebrain_ingest(summary="...", key_points=[...])│
│                                                      │
│  3. 对话结束：                                        │
│     → sidebrain_ingest(text=全文, source="claude",    │
│          session_id="claude/2026-06-03-proj")         │
│     → sidebrain_scan() 触发 GA 分析                   │
│                                                      │
│  4. 下次对话开始：                                    │
│     → sidebrain_sync_pull() 拉取最新数据               │
│     → sidebrain_search("上次讨论的...")                │
└──────────────────────────────────────────────────────┘
```

## 6. sidebrain_ingest 参数详解

### 结构化模式（直接入库 processed/）

```json
{
  "summary": "采用 Redis 替代 Memcached 作为缓存层",
  "key_points": [
    "Redis 支持持久化和集群模式",
    "Memcached 仅支持简单 KV，无法满足需求",
    "性能测试 Redis 在 10K QPS 下延迟 <1ms"
  ],
  "tags": ["架构决策", "缓存", "backend"],
  "source": "claude"
}
```

### 简单模式（写入 raw/ 等待 GA 分析）

```json
{
  "text": "完整的会话文本...",
  "source": "claude",
  "session_id": "claude/2026-06-03-myproject"
}
```

## 7. curl 测试

```bash
# 搜索
curl -s https://mcp.yhao.ccwu.cc/sidebrain \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_search","arguments":{"query":"架构","limit":3}}}'

# 备份会话
curl -s https://mcp.yhao.ccwu.cc/sidebrain \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_ingest","arguments":{"text":"测试会话内容","source":"claude","session_id":"claude/test-001"}}}'

# 触发处理
curl -s https://mcp.yhao.ccwu.cc/sidebrain \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_scan","arguments":{}}}'
```

## 8. 常见问题

**Q: 需要本地部署吗？**
A: 不需要。所有 Agent 通过 HTTP 直连云端 Sidebrain 服务。如果你想本地运行处理管线，参考「本地部署」章节。

**Q: 如何让 Agent 自动记住所有对话？**
A: 在 Agent 的系统提示词中加入 Sidebrain 规则（参考第 3 节各平台示例），再加上会话结束回调自动调 `sidebrain_ingest`。

**Q: 多个 Agent 的数据会混淆吗？**
A: 不会。通过 `source` 参数区分，每个 Agent 的数据存在独立的 `raw/<agent>/` 子目录中。

**Q: 上传后多久能被搜索到？**
A: 简单模式需要等待 GA 处理（`sidebrain_scan` 触发）。结构化模式立即可搜索。

**Q: 如何初始化本地数据？**
A: 调用 `sidebrain_sync_pull` 从服务器拉取全部已处理数据到本地。
