# Sidebrain — Pi 的侧载大脑

自动从 Pi 会话、会议纪要、用户临时输入中提取知识，
沉淀到本地知识库，并通过 MCP 协议供任意 agent 调用。

## 架构

```
[Pi sessions JSONL] ──┐
[会议纪要 .md/.txt]  ──┤──→ Sidebrain Core → ~/.sidebrain/ (数据主源)
[用户临时发送]      ──┘         │
                                ├→ ~/.pi/agent/memories/sidebrain/ (镜像)
                                ├→ ~/.pi/agent/rules/sidebrain/ (规则)
                                └→ MCP Server (stdio)
```

## 使用

```bash
# 初始化
python3 -m sidebrain init

# 健康检查
python3 -m sidebrain doctor

# 查看状态
python3 -m sidebrain status

# 查看配置（脱敏）
python3 -m sidebrain config

# 摄入 Pi 会话
python3 -m sidebrain ingest pi

# 摄入会议纪要
python3 -m sidebrain ingest meetings

# 摄入文本（stdin）
echo "一些临时想法" | python3 -m sidebrain ingest text

# 处理 raw → processed
python3 -m sidebrain process

# 同步到 Pi 记忆库
python3 -m sidebrain sync pi
```

## 来源目录

| 路径 | 用途 |
|------|------|
| `sources/meetings/` | 放会议纪要 .md / .txt |
| `sources/ad_hoc/` | 放临时文本 |

## 数据布局

所有数据在 `~/.sidebrain/`（不 git 跟踪）。

```
~/.sidebrain/
├── knowledge/
│   ├── raw/pi/          ← 原始 Pi sessions
│   ├── raw/meetings/    ← 原始会议纪要
│   ├── raw/ad_hoc/      ← 用户临时发送
│   ├── processed/       ← LLM 处理后
│   ├── index/           ← 索引
│   └── quarantine/      ← 失败文件隔离
├── state/               ← 游标、锁、度量
└── logs/                ← 日志
```

## 开发

```bash
pip install -e .
python3 -m sidebrain --help
```
