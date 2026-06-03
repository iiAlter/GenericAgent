# Sidebrain 架构重构 — Pi 执行清单

> 由 GA 于 2026-06-03 生成。任务下发：服务器 = 本机；删除 `pi_mirror`；新增 MCP `init`/`sync` 工具。

---

## 🧭 背景

**旧架构（已废弃）**：GA 主动写 `~/.pi/agent/memories/sidebrain/`（push）
**新架构**：客户端通过 MCP `init`/`sync` 拉取（pull）

**铁律**：
- GA 沉淀 → `~/.sidebrain/`（服务器主源）
- 客户端 init/sync → 通过 MCP（stdin/stdout JSON-RPC）
- GA **不主动**写任何客户端的 `~/.sidebrain/` 或 `~/.pi/agent/memories/`

---

## 📌 Issue 1: 删除 pi_mirror

**目标**：移除 GA 主动写 Pi 记忆库的代码路径

### 文件改动

| # | 文件 | 动作 |
|---|------|------|
| 1.1 | `sidebrain/sync/pi_mirror.py` | 🗑️ 删除整个文件（216 行）|
| 1.2 | `sidebrain/sync/__init__.py` | 🗑️ 删除（0 字节空文件）|
| 1.3 | `sidebrain/sync/` 目录 | 🗑️ 删除空目录 |
| 1.4 | `sidebrain/cli.py` (line 265-276) | ✂️ 删 `cmd_sync` 函数 |
| 1.5 | `sidebrain/cli.py` subparser 段 | ✂️ 删 `sync` 子命令注册（搜 `add_parser("sync"` 找到那段）|
| 1.6 | `sidebrain/daemon.py:_run_once` | ✂️ 删 sync 步骤（搜 `sync_to_pi` 或 `sync` pipeline 段，保留 ingest/process）|
| 1.7 | `sidebrain/config.py` (line 45-49) | ✂️ 删 `sync` 配置块（含 `max_items`/`dry_run`/`tags_filter`）|
| 1.8 | `config.example.yaml` (line 34-40) | ✂️ 删 `sync:` 段 |
| 1.9 | `sidebrain/paths.py` (line 40-41) | ✂️ 删 `PI_MEMORIES_MIRROR`、`PI_RULES_MIRROR` 两行 |
| 1.10 | `README.md` | ✂️ 架构图里"Pi 镜像"行；文字里的镜像说明 |
| 1.11 | `plan.md` 架构段 | ✂️ 改写为"pull 模式：客户端通过 MCP 拉取" |

### 验收

```bash
cd ~/serve/GenericAgent/sidebrain
grep -rn "pi_mirror\|PI_MEMORIES_MIRROR\|PI_RULES_MIRROR" .
# 应该 0 命中

python3 -m sidebrain --help
# 应该看不到 sync 子命令
```

---

## 📌 Issue 2: paths.py 补 2 个常量（修 3 个 import 错误）

**背景**：`sidebrain.llm`、`sidebrain.process.extractor`、`sidebrain.process.writer` 引用 paths.py 里的 `GA_MYKEY` 和 `TRASH`，找不到。

### 文件改动

[FILE:sidebrain/paths.py]

加 2 行（任意位置，建议紧跟 `GA_ROOT = ...`）：

```python
GA_MYKEY = GA_ROOT / "mykey.py"      # GA 私有 API key 文件
TRASH = SIDEBRAIN_HOME / "trash"      # 处理失败文件的隔离区
```

### 验收

```bash
python3 -c "from sidebrain.paths import GA_MYKEY, TRASH; print(GA_MYKEY, TRASH)"
python3 -c "import sidebrain.llm, sidebrain.process.extractor, sidebrain.process.writer"
# 3 个 import 都不报错
```

---

## 📌 Issue 3: 新增 MCP `init` 工具

**目标**：客户端调用 `init` 即可获取服务器元数据 + 数据 schema + 统计，告诉客户端该怎么建目录、初始 cursor 设什么

### 文件改动

[FILE:sidebrain/mcp/server.py]

### 实现

```python
# 在 _TOOL_HANDLERS 字典（line 185-187 附近）注册新工具
_TOOL_HANDLERS["sidebrain_init"] = tool_sidebrain_init

# 工具函数实现（看现有 tool_sidebrain_search 怎么写的，照抄结构）
async def tool_sidebrain_init(args: dict) -> dict:
    """返回服务器元数据 + 数据 schema + 统计 + 客户端设置建议。"""
    from sidebrain.paths import SIDEBRAIN_HOME, PROCESSED
    from sidebrain import __version__

    # 统计 processed/ 下的文件数
    count = 0
    last_mtime = 0
    if PROCESSED.exists():
        for f in PROCESSED.glob("**/*"):
            if f.is_file():
                count += 1
                last_mtime = max(last_mtime, f.stat().st_mtime)

    return {
        "server": {
            "name": "sidebrain",
            "version": __version__,
            "home": str(SIDEBRAIN_HOME),
        },
        "schema": {
            "record_format": "jsonl",
            "record_fields": ["id", "topic", "tags", "content", "source", "created_at"],
            "processed_dir": "knowledge/processed/",
        },
        "stats": {
            "total_records": count,
            "last_modified_ts": last_mtime,
        },
        "client_setup": {
            "create_dirs": ["knowledge/processed", "state"],
            "set_cursor": str(last_mtime),  # 增量起点
        },
    }
```

**注意**：根据实际代码里 `tool_*` 函数的同步/异步风格调整。

### 验收

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"sidebrain_init","arguments":{}}}' \
  | python3 -m sidebrain mcp serve
# 应该返回 server/schema/stats/client_setup JSON
```

---

## 📌 Issue 4: 新增 MCP `sync` 工具

**目标**：客户端传 cursor + limit，拉取一批 processed 数据 + next_cursor

### 文件改动

[FILE:sidebrain/mcp/server.py]

### 实现

```python
_TOOL_HANDLERS["sidebrain_sync"] = tool_sidebrain_sync

async def tool_sidebrain_sync(args: dict) -> dict:
    """增量拉取：cursor (int mtime) + limit → records + next_cursor。"""
    from sidebrain.paths import PROCESSED

    cursor = int(args.get("cursor", 0))
    limit = int(args.get("limit", 50))

    records = []
    next_cursor = None
    files = sorted(PROCESSED.glob("**/*.jsonl") if PROCESSED.exists() else [])

    for f in files:
        if len(records) >= limit:
            break
        if f.stat().st_mtime <= cursor:
            continue
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                records.append(json.loads(line))
                if len(records) >= limit:
                    break
            next_cursor = f.stat().st_mtime
        except (json.JSONDecodeError, OSError):
            continue

    has_more = len(records) >= limit

    return {
        "records": records,
        "next_cursor": str(next_cursor) if next_cursor else None,
        "has_more": has_more,
        "count": len(records),
    }
```

**注意**：`import json` 加到文件顶部。

### 验收

```bash
# 在 ~/.sidebrain/knowledge/processed/ 放一个测试 jsonl 文件
mkdir -p ~/.sidebrain/knowledge/processed
echo '{"id":"t1","topic":"test","tags":["a"],"content":"hello","source":"manual","created_at":"2026-06-03"}' \
  > ~/.sidebrain/knowledge/processed/test.jsonl

# 然后调用
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"sidebrain_sync","arguments":{"cursor":"0","limit":10}}}' \
  | python3 -m sidebrain mcp serve
# 应该返回 records 含 t1，next_cursor 是 mtime
```

---

## 📌 Issue 5: 测试

### 新建

[FILE:tests/test_mcp_init_sync.py]

```python
"""MCP init/sync 端到端测试。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SIDEBRAIN_HOME = Path.home() / ".sidebrain"
PROCESSED = SIDEBRAIN_HOME / "knowledge" / "processed"


def _call_mcp(tool: str, args: dict) -> dict:
    """通过 stdin/stdout 调一次 MCP。"""
    req = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    })
    p = subprocess.run(
        [sys.executable, "-m", "sidebrain", "mcp", "serve"],
        input=req + "\n",
        capture_output=True, text=True, timeout=30,
    )
    return json.loads(p.stdout.strip().split("\n")[-1])


def test_init_returns_server_info():
    result = _call_mcp("sidebrain_init", {})
    assert "result" in result
    info = result["result"]
    assert info["server"]["name"] == "sidebrain"
    assert "version" in info["server"]
    assert "schema" in info
    assert "stats" in info


def test_sync_returns_records():
    PROCESSED.mkdir(parents=True, exist_ok=True)
    test_file = PROCESSED / "_test_sync.jsonl"
    test_file.write_text(
        '{"id":"a","topic":"t1","tags":["x"],"content":"c1","source":"s","created_at":"2026-06-03"}\n'
        '{"id":"b","topic":"t2","tags":["y"],"content":"c2","source":"s","created_at":"2026-06-03"}\n'
    )
    try:
        result = _call_mcp("sidebrain_sync", {"cursor": "0", "limit": 10})
        assert "result" in result
        assert result["result"]["count"] >= 2
    finally:
        test_file.unlink()
```

### 验收

```bash
cd ~/serve/GenericAgent/sidebrain
pytest tests/test_mcp_init_sync.py -v
# 应该 2 个测试都过
```

---

## 📊 总验收（全部做完后跑一次）

```bash
cd ~/serve/GenericAgent/sidebrain

# 1. import 全通
python3 -c "import sidebrain.llm, sidebrain.process.extractor, sidebrain.process.writer, sidebrain.mcp.server"
echo "✓ import 全通"

# 2. 没残留
grep -rn "pi_mirror\|PI_MEMORIES_MIRROR\|PI_RULES_MIRROR" . && echo "✗ 有残留" || echo "✓ 无残留"

# 3. sync 命令消失
python3 -m sidebrain --help | grep -q "sync" && echo "✗ sync 还在" || echo "✓ sync 已删"

# 4. doctor 通过
python3 -m sidebrain doctor

# 5. 测试通过
pytest tests/ -v
```

---

## 🔄 Pi 工作流

1. 一个一个 issue 做，每做完跑该 issue 的验收命令
2. 全部做完后跑总验收
3. 报结果给 GA（哪些过哪些没过，失败贴错误堆栈）
4. 失败的话由 GA 帮诊断

---

**生成时间**：2026-06-03
**生成者**：GenericAgent
**代码库**：`~/serve/GenericAgent/sidebrain`
