# Sidebrain MCP Server — 第三方接入说明

Sidebrain 是一个跨 Agent 共享知识库，通过 MCP（Model Context Protocol）对外提供服务。
任何支持 MCP 协议的客户端都可以直接连接使用。

---

## 快速接入

### 服务地址

```
https://mcp_sidebrain.yhao.ccwu.cc/mcp
```

### 可用工具（5 个）

| 工具 | 说明 | 谁用 |
|------|------|------|
| `sidebrain_search` | 搜索知识库，查找之前讨论过的决策/架构/配置 | 需要回溯上下文时 |
| `sidebrain_get` | 获取单条记忆的完整详情 | 查看某条结果的具体内容 |
| `sidebrain_ingest` | 将重要信息存入知识库 | 有需要记住的信息时 |
| `sidebrain_list_projects` | 列出知识库中所有条目 | 浏览有什么内容 |
| `sidebrain_scan` | 触发服务端 GA 扫描新数据 | 需要处理新 Pi 会话时 |

---

## 各客户端配置

### Claude Desktop

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "sidebrain": {
      "url": "https://mcp_sidebrain.yhao.ccwu.cc/mcp"
    }
  }
}
```

### Cursor

在 Cursor 设置中添加 MCP Server：

```
名称: sidebrain
类型: HTTP
地址: https://mcp_sidebrain.yhao.ccwu.cc/mcp
```

### 自定义代码（TypeScript）

```typescript
const response = await fetch('https://mcp_sidebrain.yhao.ccwu.cc/mcp', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    jsonrpc: '2.0',
    id: '1',
    method: 'tools/call',
    params: {
      name: 'sidebrain_search',
      arguments: { query: '架构', limit: 5 }
    }
  })
});
const data = await response.json();
console.log(data.result.content[0].text);
```

### 自定义代码（Python）

```python
import requests, json

resp = requests.post('https://mcp_sidebrain.yhao.ccwu.cc/mcp', json={
    'jsonrpc': '2.0',
    'id': '1',
    'method': 'tools/call',
    'params': {
        'name': 'sidebrain_search',
        'arguments': {'query': '架构', 'limit': 5}
    }
})
print(resp.json()['result']['content'][0]['text'])
```

### 命令行（curl）

```bash
# 搜索
curl -s https://mcp_sidebrain.yhao.ccwu.cc/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_search","arguments":{"query":"架构","limit":3}}}'

# 写入
curl -s https://mcp_sidebrain.yhao.ccwu.cc/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_ingest","arguments":{"summary":"决策记录","key_points":["要点1","要点2"],"tags":["project-x"]}}}'

# 列出所有条目
curl -s https://mcp_sidebrain.yhao.ccwu.cc/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_list_projects","arguments":{}}}'

# 获取详情
curl -s https://mcp_sidebrain.yhao.ccwu.cc/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_get","arguments":{"topic_id":"关键词"}}}'

# 触发服务端扫描
curl -s https://mcp_sidebrain.yhao.ccwu.cc/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"sidebrain_scan","arguments":{}}}'
```

---

## 工具参数说明

### sidebrain_search

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | 搜索关键词 |
| `limit` | number | 否 | 最大返回条数，默认 10 |

### sidebrain_ingest（结构化模式）

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `summary` | string | 是 | 摘要/标题 |
| `key_points` | string[] | 否 | 关键点列表 |
| `action_items` | string[] | 否 | 行动项列表 |
| `decisions` | object[] | 否 | 决策列表 |
| `tags` | string[] | 否 | 标签 |
| `source` | string | 否 | 来源标识 |

### sidebrain_get

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `topic_id` | string | 是 | 记忆 ID 或文件名关键词 |

---

## 初始化流程

MCP 客户端需要先发送 `initialize` 请求建立会话：

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": { "name": "my-agent", "version": "1.0" }
  }
}
```

成功后可以调用 `tools/list` 获取工具列表：

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "tools/list",
  "params": {}
}
```

---

## 最佳实践

**什么时候搜索：**
- 用户说"之前/上次/记得/讨论过/提到过"
- 需要了解项目背景或历史决策
- 不确定某些配置或架构是否已经讨论过

**什么时候写入：**
- 用户分享了重要的决定或笔记
- 讨论了架构方案或技术选型
- 发现了有用的配置或环境事实
