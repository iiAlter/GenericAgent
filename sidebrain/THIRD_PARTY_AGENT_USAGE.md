# Sidebrain 使用与第三方 Agent 接入教程

本文说明两件事：

- 你本地 Pi 里如何使用 Sidebrain
- 其他第三方 agent 如何通过 MCP 使用 Sidebrain

Sidebrain 的事实源是 `~/.sidebrain/`。Pi 和第三方 agent 默认通过 MCP 查询或写入，不直接改 Pi 镜像目录。

## 1. 你本地 Pi 如何使用

### 1.1 本地 Pi 已具备的入口

本机 Pi 扩展文件：

```text
~/.pi/agent/extensions/sidebrain.ts
```

它会注册：

| 类型 | 名称 | 用途 |
|------|------|------|
| Slash command | `/sidebrain` | 手动上传、查状态、触发处理、开关自动上传 |
| Tool | `sidebrain_search` | 搜索历史记忆 |
| Tool | `sidebrain_get` | 获取单条详情 |
| Tool | `sidebrain_list_projects` | 列出项目/来源 |
| Tool | `sidebrain_ingest` | 写入重要信息 |
| Tool | `sidebrain_scan` | 触发服务端处理 |
| Tool | `sidebrain_sync_pull` | 拉取服务端数据到本地 |

改完扩展后，在 Pi 里执行：

```text
/reload
```

### 1.2 常用命令

```text
/sidebrain scan
```

上传本机 `~/.pi/agent/sessions/**/*.jsonl` 里新增或变更的 Pi 会话到服务端 Sidebrain raw 区。扩展会用游标文件避免重复上传：

```text
~/.pi/sidebrain_scan_cursor.json
```

```text
/sidebrain status
```

查看 Sidebrain 当前项目/来源列表。底层调用 `sidebrain_list_projects`。

```text
/sidebrain process
```

触发服务端处理 raw 数据。底层调用远程 `sidebrain_scan`，服务端会启动 GA 分析并写入 `processed/`。

```text
/sidebrain auto on
/sidebrain auto off
/sidebrain auto
```

开启、关闭或查看自动上传状态。当前自动上传间隔是 600 秒。Pi 每次 session start 后也会延迟约 5 秒自动执行一次上传。

### 1.3 在 Pi 对话里怎么让 Agent 用

你不需要手动输入工具 JSON。直接用自然语言即可，例如：

```text
先查一下 sidebrain 里有没有之前关于 R9 风险的记录
```

```text
查一下上次 sidebrain MCP fetch failed 是怎么修的
```

```text
把这个结论记到 sidebrain：daemon 默认不再 sync 到 Pi 镜像，只保留手动 sidebrain sync
```

```text
用 sidebrain_get 看一下 ID b6704616-410e-4f8a-a60e-238eeaf28468 的详情
```

Pi 会根据工具说明自动调用 `sidebrain_search`、`sidebrain_get` 或 `sidebrain_ingest`。

### 1.4 Pi 工具参数

`sidebrain_search` 支持：

| 参数 | 类型 | 说明 |
|------|------|------|
| `query` | string | 搜索关键词 |
| `limit` | number | 返回条数，默认 10 |
| `status` | string | `active/resolved/superseded/archived/all`，默认 `active` |
| `type` | string | 记忆类型，如 `decision`、`problem-solution`、`gotcha`、`note` |
| `topic_key` | string | 按稳定主题键精确过滤 |

`sidebrain_get` 支持：

| 参数 | 类型 | 说明 |
|------|------|------|
| `topic_id` | string | 记忆 ID、`topic_key` 或文件名关键词 |

`sidebrain_ingest` 支持两种模式：

简单模式，上传原始文本等待处理：

```json
{
  "text": "完整会话或笔记文本",
  "source": "pi-session"
}
```

结构化模式，直接写入已处理知识库：

```json
{
  "summary": "一句话摘要",
  "key_points": ["关键点"],
  "action_items": ["行动项"],
  "decisions": [{"decision": "决策", "reasoning": "原因"}],
  "tags": ["sidebrain"],
  "topic_key": "architecture/sidebrain-mcp",
  "type": "decision",
  "status": "active",
  "confidence": 0.9
}
```

### 1.5 本地 HTTP MCP 服务

本机还有一个 systemd 管理的 Node HTTP MCP 服务：

```bash
systemctl --user status sidebrain-mcp.service
```

默认本地地址：

```text
http://127.0.0.1:19000/mcp
```

也支持：

```text
http://127.0.0.1:19000/
http://127.0.0.1:19000/sidebrain
```

这个服务直接读写 `SIDEBRAIN_HOME`，默认是：

```text
~/.sidebrain
```

## 2. 第三方 Agent 如何接入

### 2.1 鉴权

远程请求需要 Bearer Token：

```
Authorization: Bearer <token>
```

本地 localhost 请求（`127.0.0.1:19000`）当前不强制鉴权。

Token 生成：

```bash
node ~/.pi/agent/extensions/sidebrain-mcp-server.mjs --token-generate
```

### 2.2 选择接入地址

如果第三方 agent 和 Sidebrain 在同一台机器上，优先用本地地址：

```text
http://127.0.0.1:19000/mcp
```

如果在其他机器上，使用公开 HTTP MCP 地址：

```text
https://mcp.yhao.ccwu.cc/sidebrain
```

### 2.3 MCP 客户端配置

支持 HTTP MCP URL 的客户端，一般配置如下：

```json
{
  "mcpServers": {
    "sidebrain": {
      "url": "https://mcp.yhao.ccwu.cc/sidebrain"
    }
  }
}
```

本机客户端可改成本地地址：

```json
{
  "mcpServers": {
    "sidebrain": {
      "url": "http://127.0.0.1:19000/mcp"
    }
  }
}
```

### 2.4 JSON-RPC 调用方式

初始化：

```json
{
  "jsonrpc": "2.0",
  "id": "init-1",
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "my-agent", "version": "1.0.0"}
  }
}
```

列工具：

```json
{
  "jsonrpc": "2.0",
  "id": "tools-1",
  "method": "tools/list",
  "params": {}
}
```

调用工具：

```json
{
  "jsonrpc": "2.0",
  "id": "call-1",
  "method": "tools/call",
  "params": {
    "name": "sidebrain_search",
    "arguments": {
      "query": "sidebrain",
      "limit": 5,
      "status": "active"
    }
  }
}
```

### 2.5 curl 示例

搜索（远程需加 token）：

```bash
# 本地
curl -s http://127.0.0.1:19000/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_search","arguments":{"query":"sidebrain","limit":3,"status":"all"}}}'

# 远程
curl -s https://mcp.yhao.ccwu.cc/sidebrain \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $SIDEBRAIN_TOKEN" \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_search","arguments":{"query":"sidebrain","limit":3,"status":"all"}}}'
```

按类型搜索：

```bash
curl -s http://127.0.0.1:19000/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_search","arguments":{"query":"","type":"decision","status":"active","limit":10}}}'
```

获取详情：

```bash
curl -s http://127.0.0.1:19000/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_get","arguments":{"topic_id":"architecture/sidebrain-mcp"}}}'
```

写入结构化记忆：

```bash
curl -s http://127.0.0.1:19000/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_ingest","arguments":{"summary":"采用纯 MCP pull 作为默认 Sidebrain 架构","key_points":["daemon 不主动写 Pi 镜像","Pi 和第三方 agent 通过 MCP 查询"],"tags":["sidebrain","architecture"],"topic_key":"architecture/sidebrain-pull","type":"decision","status":"active","confidence":0.9}}}'
```

上传原始会话，等待 GA 后续处理：

```bash
curl -s http://127.0.0.1:19000/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_ingest","arguments":{"text":"完整会话文本","source":"claude","session_id":"claude/2026-06-04-my-project"}}}'
```

### 2.6 Python 示例

```python
import requests, os

# 本地用 localhost，远程用 mcp.yhao.ccwu.cc/sidebrain
SIDEBRAIN_URL = "http://127.0.0.1:19000/mcp"
TOKEN = os.environ.get("SIDEBRAIN_TOKEN", "")


def sidebrain_call(name: str, arguments: dict) -> str:
    headers = {"Content-Type": "application/json"}
    if TOKEN and "127.0.0.1" not in SIDEBRAIN_URL:
        headers["Authorization"] = f"Bearer {TOKEN}"
    resp = requests.post(
        SIDEBRAIN_URL,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if "error" in payload:
        raise RuntimeError(payload["error"])
    content = payload.get("result", {}).get("content", [])
    return "\n".join(item.get("text", "") for item in content if item.get("type") == "text")


print(sidebrain_call("sidebrain_search", {
    "query": "R9 风险",
    "limit": 5,
    "status": "all",
}))

print(sidebrain_call("sidebrain_ingest", {
    "summary": "第三方 agent 已接入 Sidebrain",
    "key_points": ["通过 HTTP MCP 调用 tools/call", "会话结束时上传 summary 或全文"],
    "tags": ["sidebrain", "third-party-agent"],
    "topic_key": "integration/third-party-agent",
    "type": "what-changed",
    "status": "active",
}))
```

### 2.7 TypeScript 示例

```typescript
const SIDEBRAIN_URL = "http://127.0.0.1:19000/mcp";
const TOKEN = process.env.SIDEBRAIN_TOKEN || "";

async function sidebrainCall(name: string, arguments_: Record<string, unknown>) {
  const headers: Record<string, string> = {"Content-Type": "application/json"};
  if (TOKEN && !SIDEBRAIN_URL.includes("127.0.0.1")) {
    headers["Authorization"] = `Bearer ${TOKEN}`;
  }
  const resp = await fetch(SIDEBRAIN_URL, {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: "1",
      method: "tools/call",
      params: {name, arguments: arguments_},
    }),
  });
  const payload = await resp.json();
  if (payload.error) throw new Error(payload.error.message);
  return payload.result.content
    .filter((item: {type: string}) => item.type === "text")
    .map((item: {text: string}) => item.text)
    .join("\n");
}

const result = await sidebrainCall("sidebrain_search", {
  query: "sidebrain MCP",
  limit: 5,
  status: "active",
});
console.log(result);
```

## 3. 可用工具详解

### sidebrain_search

搜索 processed 记忆。当前排序权重：

1. `topic_key` 精确命中
2. `topic_key` 部分命中
3. `summary`
4. `tags`
5. `key_points`
6. 全文

参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `query` | string | 是 | 无 | 搜索关键词，可为空字符串配合过滤条件 |
| `limit` | number | 否 | 10 | 最大返回条数 |
| `status` | string | 否 | `active` | `active/resolved/superseded/archived/all` |
| `type` | string | 否 | 空 | `note/decision/problem-solution/gotcha/what-changed/trade-off` |
| `topic_key` | string | 否 | 空 | 稳定主题键精确过滤 |

### sidebrain_get

获取单条详情，并展开：

- `related_entries`
- `supersedes_entries`
- `superseded_by_entry`

参数：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `topic_id` | string | 是 | 记忆 ID、`topic_key` 或文件名关键词 |

### sidebrain_ingest

两种模式：

| 模式 | 必填 | 写入位置 | 适用场景 |
|------|------|----------|----------|
| 原始文本模式 | `text` | `knowledge/raw/<source>/` | 上传完整会话，等待 GA 抽取 |
| 结构化模式 | `summary` | `knowledge/processed/` | agent 已经总结好，直接入库 |

结构化字段：

| 字段 | 说明 |
|------|------|
| `summary` | 一句话摘要 |
| `key_points` | 关键点 |
| `action_items` | 行动项 |
| `decisions` | 决策对象数组，建议含 `decision/reasoning/alternatives` |
| `tags` | 标签 |
| `topic_key` | 稳定主题键；相同 active topic_key 会更新旧记录 |
| `type` | 记忆类型 |
| `status` | 生命周期状态 |
| `confidence` | 0 到 1 |

### sidebrain_update / resolve / supersede

治理已有记忆：

```json
{
  "name": "sidebrain_resolve",
  "arguments": {
    "topic_id": "integration/old-plan",
    "reason": "已完成"
  }
}
```

```json
{
  "name": "sidebrain_supersede",
  "arguments": {
    "topic_id": "architecture/old",
    "superseded_by": "architecture/new",
    "reason": "新架构替代旧方案"
  }
}
```

`resolved` 和 `superseded` 默认不会被 `sidebrain_search` 返回，除非传 `status="all"`。

## 4. 第三方 Agent 推荐工作流

### 会话开始

当用户提到历史上下文，或任务可能受历史决策影响时：

```text
先 sidebrain_search(query=<项目名或关键词>, status="active")
```

### 做决策前

```text
sidebrain_search(query=<架构/模块/问题关键词>, type="decision", status="all")
```

如果查到相关条目，再：

```text
sidebrain_get(topic_id=<搜索结果 ID 或 topic_key>)
```

### 会话结束

保存有价值信息，优先结构化写入：

```json
{
  "summary": "本轮完成 Sidebrain 第三方 agent 接入教程",
  "key_points": ["Pi 本地使用方式已确认", "第三方 agent 通过 HTTP MCP 接入"],
  "tags": ["sidebrain", "docs"],
  "topic_key": "docs/third-party-agent-usage",
  "type": "what-changed",
  "status": "active"
}
```

如需保留全文，则再上传原始文本：

```json
{
  "text": "<完整会话>",
  "source": "my-agent",
  "session_id": "my-agent/2026-06-04-project"
}
```

## 5. 不建议写入的内容

不要把这些内容写入 Sidebrain：

- `ls`、`pwd`、`git status` 等无长期价值命令
- 一次性临时输出
- 明文密钥、token、密码
- 未经用户允许的隐私内容
- 大段第三方版权文本

## 6. 故障排查

检查本地服务：

```bash
systemctl --user status sidebrain-mcp.service --no-pager
```

重启本地服务：

```bash
systemctl --user restart sidebrain-mcp.service
```

列出工具：

```bash
curl -s http://127.0.0.1:19000/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/list","params":{}}'
```

Sidebrain 健康检查：

```bash
cd ~/serve/GenericAgent/sidebrain
python3 -m sidebrain health
```

Pi 扩展变更后：

```text
/reload
```

## 7. 当前本机事实

- Pi 扩展：`~/.pi/agent/extensions/sidebrain.ts`
- Node MCP 服务：`~/.pi/agent/extensions/sidebrain-mcp-server.mjs`
- systemd 服务：`sidebrain-mcp.service`
- 本地 MCP 地址：`http://127.0.0.1:19000/mcp`
- 公开 MCP 地址：`https://mcp.yhao.ccwu.cc/sidebrain`
- 主库：`~/.sidebrain`
