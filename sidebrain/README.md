# Sidebrain — GA 的侧载大脑

自动从 Pi 会话、会议纪要、临时输入中提取结构化知识，
沉淀到 `~/.sidebrain/`，通过 MCP 协议供 Pi 和第三方 agent 调用。

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                     数据摄入 (Ingest)                         │
│                                                             │
│  Pi sessions (.jsonl) ──→ pi_watcher ──→ raw/pi/*.md       │
│  Meeting notes (.md)  ──→ meeting_watcher ──→ raw/meetings/ │
│  Ad-hoc text          ──→ ad_hoc ──→ raw/ad_hoc/           │
│                                                             │
│  游标驱动增量扫描，原子写入，失败进 quarantine/               │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    AI 处理 (Process)                          │
│                                                             │
│  process_pipeline.py                                        │
│    1. 扫描 raw/ 下新文件（按 content hash 去重）              │
│    2. 逐个 spawn GA agentmain.py --task                      │
│    3. GA 读取文件 → LLM 提取摘要/关键点/决策 → 写 JSON        │
│    4. Python 校验/规范化 JSON → writer 结构化入库              │
│                                                             │
│  raw/ ──→ ~/.sidebrain/knowledge/processed/<tag>/*.md       │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   MCP 服务层（双实现）                         │
│                                                             │
│  Python MCP (mcp/server.py)     Node.js MCP (.mjs)          │
│  ├─ stdio JSON-RPC              ├─ stdio JSON-RPC           │
│  ├─ 直读 processed/*.md         ├─ 直读 processed/*.md      │
│  ├─ status/type/topic_key 过滤   ├─ status/type/topic_key 过滤│
│  └─ related/supersedes 展开      └─ related/supersedes 展开 │
│  └─ GA 本地使用                  ├─ Pi extension 集成        │
│                                  ├─ HTTP 模式（systemd）     │
│                                  └─ 远程客户端接入            │
│                                                             │
│  GA tools (ga.py):                Pi tools (sidebrain.ts):  │
│    do_sidebrain_search              sidebrain_search        │
│    do_sidebrain_get                 sidebrain_get           │
│    do_sidebrain_ingest              sidebrain_ingest        │
│    do_sidebrain_list_projects       sidebrain_scan          │
│                                     sidebrain_sync_pull     │
└─────────────────────────────────────────────────────────────┘
```

## 数据目录

```
~/.sidebrain/                     ← GA 私有主源 (SIDEBRAIN_HOME)
├── knowledge/
│   ├── processed/                ← LLM 处理后的结构化知识 (.md)
│   │   ├── pi-session/           ← 按 source / tag 分子目录
│   │   ├── sidebrain/
│   │   └── ...
│   ├── raw/                      ← 原始待处理数据
│   │   ├── pi/                   ← Pi 会话解析后的 markdown
│   │   ├── meetings/             ← 会议纪要
│   │   └── ad_hoc/               ← 临时输入
│   └── quarantine/               ← 处理失败隔离
├── state/                        ← 游标、锁、PID 文件
└── logs/                         ← 按日滚动日志
```

**数据格式**：每份 processed 文件是带 JSON frontmatter 的 Markdown：

```
---{"schema_version":1, "id":"uuid", ...}---

```json
{"summary": "...", "key_points": [...], "decisions": [...]}
```
```

## 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **路径常量** | `paths.py` | 全代码库单点引用，`SIDEBRAIN_HOME` 可被环境变量覆盖 |
| **会话解析** | `ingest/pi_parser.py` | jsonl → 结构化 markdown（按 turn 分组） |
| **增量摄入** | `ingest/pi_watcher.py` | 游标驱动增量扫描 Pi sessions |
| **会议摄入** | `ingest/meeting_watcher.py` | 游标驱动增量扫描会议纪要 |
| **临时摄入** | `ingest/ad_hoc.py` | stdin/参数直写 |
| **处理管线** | `process_pipeline.py` | 扫描 raw → spawn GA → 读取 JSON → 校验后写 processed |
| **抽取校验** | `process/validate.py` | 规范化 GA JSON，拒绝缺摘要/坏状态/坏置信度，失败进 quarantine |
| **写入引擎** | `process/writer.py` | JSON frontmatter + body markdown，校验 + 原子写入 + quarantine |
| **去重** | `process/dedup.py` | content hash 去重，`list_projects()` |
| **LLM 封装** | `llm.py` | 调用 GA 的 llmcore（支持 native_oai / mixin 配置） |
| **MCP 服务** | `mcp/server.py` | Python MCP server（stdio JSON-RPC），4 个工具 |
| **MCP 客户端** | `mcp_client.py` | Python 端通过子进程调 Node.js MCP server |
| **守护进程** | `daemon.py` | 定时执行 ingest → process（PID 锁 + 信号处理） |
| **健康检查** | `health.py` | `sidebrain health` 快速报告 |
| **CLI** | `cli.py` | 所有子命令入口 |
| **Pi 扩展** | `~/.pi/agent/extensions/sidebrain.ts` | Pi 内 /sidebrain 命令 + 5 个工具 |
| **Node.js MCP** | `~/.pi/agent/extensions/sidebrain-mcp-server.mjs` | HTTP MCP server（systemd 管理） |

## CLI 使用

```bash
# 初始化目录结构
python3 -m sidebrain init

# 健康自检（依赖/路径/LLM/MCP）
python3 -m sidebrain doctor

# 查看状态（raw/processed/quarantine 计数）
python3 -m sidebrain status

# 摄入
python3 -m sidebrain ingest pi              # 增量摄入 Pi 会话
python3 -m sidebrain ingest meetings        # 摄入会议纪要
echo "临时想法" | python3 -m sidebrain ingest text

# AI 处理（raw → processed，每次调 GA 分析一个文件）
python3 -m sidebrain process
python3 -m sidebrain process --dry-run      # 仅预览

# MCP 管理
python3 -m sidebrain mcp serve              # stdio 模式
python3 -m sidebrain mcp serve-http         # HTTP 模式（端口 19000）
python3 -m sidebrain mcp install            # 注册到 Pi
python3 -m sidebrain mcp status

# 守护进程（每 300s 自动跑 ingest + process）
python3 -m sidebrain daemon start
python3 -m sidebrain daemon status
python3 -m sidebrain daemon stop

# 可选手动导出（把精选 processed 条目写到 Pi 镜像目录）
python3 -m sidebrain sync --dry-run
python3 -m sidebrain sync

# 快速健康检查
python3 -m sidebrain health
```

## 接入教程

- Pi 本地使用方式和第三方 agent 接入教程：[`THIRD_PARTY_AGENT_USAGE.md`](./THIRD_PARTY_AGENT_USAGE.md)
- 旧版 MCP 快速说明：[`MCP_USAGE.md`](./MCP_USAGE.md)

## 生产部署

```bash
# systemd 定时任务（每天凌晨 2:00 处理）
systemctl --user enable sidebrain-process.timer
systemctl --user start sidebrain-process.timer

# systemd 常驻（Node.js MCP HTTP server）
systemctl --user enable sidebrain-mcp.service
systemctl --user start sidebrain-mcp.service
```

## 处理流程详解

### 1. 摄入阶段（无 AI，纯解析）

- **Pi 会话**：`pi_watcher` 按 mtime 增量扫描 `~/.pi/agent/sessions/**/*.jsonl`，由 `pi_parser` 解析为结构化 markdown（按 role 分组，保留 message/tool_calls），原子写入 `raw/pi/<session_id>.md`
- **会议纪要**：`meeting_watcher` 扫描 `sources/meetings/*.{md,txt}`，直接复制到 `raw/meetings/`
- **临时文本**：stdin 或参数，直接写入 `raw/ad_hoc/`

### 2. 处理阶段（GA + LLM 分析）

`process_pipeline.py` 的核心流程：

1. 扫描 `raw/pi/`、`raw/meetings/`、`raw/ad_hoc/` 下所有 `.md` 和 `.jsonl`
2. 按 `content_hash` (SHA-256) 去重，跳过已处理文件
3. 对每个新文件，构建 prompt 并 spawn GA：
   ```bash
   python3 agentmain.py --task sidebrain_pi_session_<id> --nobg
   ```
4. GA 读取文件 → LLM 分析 → 写入 JSON 到 `temp/_<id>_extracted.json`
5. Python 读取 JSON，先规范化字段并校验必填摘要、状态、置信度和决策结构
6. 合格结果调用 `write_processed()` 写入 `processed/<tag>/<title>_<id>.md`，不合格结果写入 `quarantine/validation__*.json`
7. 更新 `state/process_cursor.json` 游标

### 3. 去重策略

- **摄入层**：游标按 mtime 增量，避免重复扫描
- **处理层**：content hash（SHA-256）去重，已处理文件永久跳过
- **写入层**：`writer.py` 生成稳定文件名（标题 + ID 前缀）

## GA 集成

`ga.py` 通过 `GenericAgentHandler` 注册 4 个 sidebrain 工具，底层调用 `sidebrain/mcp_client.py`（Python 端通过子进程与 Node.js MCP server 通信）：

```python
# ga.py:526-596
def do_sidebrain_search(self, args, response):   # 搜索知识库
def do_sidebrain_get(self, args, response):      # 获取详情
def do_sidebrain_ingest(self, args, response):   # 摄入信息
def do_sidebrain_list_projects(self, args, response):  # 列出项目
```

## MCP 工具表

| 工具 | Python 端 | Node.js 端 | 说明 |
|------|-----------|------------|------|
| `sidebrain_search` | ✅ `mcp/server.py` | ✅ `.mjs` | 加权搜索已处理条目，支持 status/type/topic_key 过滤 |
| `sidebrain_get` | ✅ | ✅ | 获取单条详情，并展开 related/supersedes 关联 |
| `sidebrain_ingest` | ✅ | ✅ | 摄入文本 |
| `sidebrain_list_projects` | ✅ | ✅ | 列出项目 |
| `sidebrain_scan` | ❌ | ✅ `.mjs` + `.ts` | 触发后台处理 |
| `sidebrain_sync_pull` | ❌ | ✅ `.mjs` + `.ts` | 从远程拉取 |

> **双实现原因**：Python server 给 GA 本地用（直读文件，零开销）；Node.js server 供 Pi 集成（TUI 命令注册）和远程 HTTP 客户端（支持 `--http` 模式）。

## 关键设计决策

- **数据归属**：`~/.sidebrain/` 是唯一主源。Pi 和第三方 agent 通过 MCP pull 查询/拉取，GA/daemon 不主动写 Pi 目录
- **AI 分析**：使用 GA 的 `agentmain.py --task` 模式（非直接 tool call），因为 DeepSeek function calling 限制导致 sidebrain_ingest 工具不可见。GA 用 `file_write` 写 JSON 中间文件，Python 端读 JSON 入库
- **stdio vs HTTP**：Python MCP 用 stdio（GA 子进程），Node.js MCP 支持两种模式（systemd HTTP 服务 + Pi 内部 stdio）
- **Pi 镜像是可选导出**：`sidebrain sync` 只作为手动导出/降级缓存能力保留；daemon 默认不执行 sync，避免 `~/.sidebrain/` 与 Pi 镜像出现双主源
- **跨平台路径**：`sidebrain.ts` 的 `_doScan` 统一使用 `replace(/\\/g, "/")` 处理 Windows 反斜杠

## 变更日志

### 2026-06-03
- **超时修复**: `sidebrain.ts` 的 `_doScan` 添加 15s AbortController 超时 + 进度/错误通知
- **GA 集成**: `ga.py` 新增 4 个 `do_sidebrain_*` handler，通过 `mcp_client` 调用 MCP
- **process_pipeline**: GA 用 file_write 写 JSON 中间文件方案（解决 DeepSeek tool call 限制）
- **路径统一**: `~/.sidebrain/` 为 GA 主源（`paths.py`），Node.js 端同步
- **默认纯 MCP pull**: daemon 移除自动 sync；`sidebrain sync` 保留为可选手动导出
- **检索优先级**: 搜索按 `topic_key` 精确/部分命中、summary、tags、key_points、全文依次加权；默认只返回 active 记忆
