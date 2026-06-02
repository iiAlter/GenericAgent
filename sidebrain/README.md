# Sidebrain — Pi 的侧载大脑

自动从 Pi 会话、会议纪要、用户临时输入中提取知识，
沉淀到本地知识库，通过 MCP 协议供任意 agent 调用。

## 架构

```
[Pi sessions JSONL] ──┐
[会议纪要 .md/.txt]  ──┤──→ sidebrain_scan → GA → sidebrain_ingest → ~/.pi/sidebrain/knowledge/processed/
[用户临时发送]      ──┘                                         │
                                                               ├→ MCP Server (stdio + HTTP)
                                                               └→ Pi 工具 (sidebrain_search/ingest/...)
```

## 数据路径（2026-06-03 统一）

所有路径统一为 `~/.pi/sidebrain/`（GA 和 Pi 共享）。

```
~/.pi/sidebrain/
├── knowledge/
│   ├── processed/       ← LLM 处理后的结构化知识
│   ├── raw/pi/          ← 原始 Pi sessions
│   ├── raw/meetings/    ← 原始会议纪要
│   ├── raw/ad_hoc/      ← 用户临时发送
│   └── quarantine/      ← 失败文件隔离
├── state/               ← 游标、锁
└── logs/                ← 日志
```

## GA 工具集成

GA (`agentmain.py`) 通过 `GenericAgentHandler` 注册了以下 sidebrain 工具：

| 工具 | 实现位置 | 说明 |
|------|----------|------|
| `sidebrain_search` | `ga.py:do_sidebrain_search()` | 搜索知识库 |
| `sidebrain_get` | `ga.py:do_sidebrain_get()` | 获取单条记忆详情 |
| `sidebrain_ingest` | `ga.py:do_sidebrain_ingest()` | 摄入信息到知识库 |
| `sidebrain_list_projects` | `ga.py:do_sidebrain_list_projects()` | 列出项目 |

所有工具通过 `sidebrain/mcp_client.py` 调用 MCP server（stdio JSON-RPC）。

## 使用

```bash
# 初始化
python3 -m sidebrain init

# 查看状态
python3 -m sidebrain status

# 摄入会议纪要
python3 -m sidebrain ingest meetings

# 摄入文本（stdin）
echo "临时想法" | python3 -m sidebrain ingest text

# 手动处理 raw → processed
python3 -m sidebrain process
```

## 来源目录

| 路径 | 用途 |
|------|------|
| `sources/meetings/` | 放会议纪要 .md / .txt，`sidebrain_scan` 自动扫描 |
| `sources/ad_hoc/` | 放临时文本 |

## MCP 客户端

```python
from sidebrain.mcp_client import search, get_detail, ingest, list_projects

# 搜索
result = search("MCP 架构", limit=10)

# 摄入
ingest("重要的技术决策...", source="meeting")

# 列出项目
projects = list_projects()
```

MCP server 启动方式：
- stdio 模式（默认）：`node ~/.pi/agent/extensions/sidebrain-mcp-server.mjs`
- HTTP 模式：`node ~/.pi/agent/extensions/sidebrain-mcp-server.mjs --http`（监听 `SIDEBRAIN_PORT`，默认 19000）

## 变更日志

### 2026-06-03
- **GA 集成**: `ga.py` 新增 4 个 `do_sidebrain_*` handler，通过 `mcp_client` 调用 MCP server
- **路径统一**: Python 模块默认路径从 `~/.sidebrain/` 改为 `~/.pi/sidebrain/`，与 Node.js 端一致
- **格式修复**: 简单模式 ingest 改为 Pi 原生 YAML frontmatter 格式，确保 `loadAllEntries` 可解析
- **list_projects 改进**: 改用 `loadAllEntries()` 按 source/project 聚合，不再返回裸文件名
- **scan prompt 优化**: 明确告知 GA 可用的 sidebrain 工具列表
