# PawGit

PawGit 是 QwenPaw 的会话状态版本控制插件。它在工作区的 `.pawgit/` 下维护一个私有影子 Git 仓库，对工作区做全量快照，并支持查看时间线、预览/执行回滚、可选的记忆回滚、GC 清理和重置 PawGit 状态。

---

## 功能特性

| 功能 | 说明 |
| --- | --- |
| 自动检查点 | Agent 每次回复后，按防抖间隔创建 workspace 全量快照 |
| 永久快照 | 用户命名的 `snapshot`，GC 不会删除 |
| 时间线 | 按 auto / snapshot / pre-rewind 分类展示，含 ASCII DAG |
| 会话回滚 | 仅还原当前频道/会话的 JSON |
| 记忆回滚 | 还原当前会话 + `MEMORY.md` + `memory/` |
| Agent 工具 | 供 Agent 创建快照、查看时间线、预览回滚、GC 试运行等 |
| 垃圾回收 | 按策略清理可回收的 `auto` 和 `pre-rewind` ref |
| 重置 | 删除并重建 `.pawgit/`，不改动会话或记忆文件 |

---

## 斜杠命令

PawGit 提供统一的 `/pawgit` 命令：

```text
/pawgit timeline [--limit=N] [--all]
/pawgit snapshot [message]
/pawgit rewind <N|snap_name|sha> [--dry-run]
/pawgit rewind <target> --include-memory [--dry-run|--confirm]
/pawgit gc [--compact] [--all-sessions] [--dry-run]
/pawgit reset --confirm
/pawgit --help
```

不带参数运行 `/pawgit` 也会显示帮助。

常见流程：

```text
/pawgit timeline
/pawgit snapshot before-refactor
/pawgit rewind before-refactor --dry-run
/pawgit rewind before-refactor
```

---

## Agent 工具

插件注册了名为 `pawgit` 的 Agent 工具，适用于：

- 有风险的操作前创建快照
- 查看时间线、预览回滚目标
- GC 试运行或确认后执行 GC
- 显式确认后重置 PawGit

示例：

```text
pawgit(action="snapshot", message="before-risky-change")
pawgit(action="timeline", limit=10)
pawgit(action="rewind", target="1", dry_run=true)
pawgit(action="rewind", target="1", include_memory=true, dry_run=true)
pawgit(action="gc", dry_run=true)
pawgit(action="gc", dry_run=false, confirm=true)
pawgit(action="reset", confirm=true)
```

**真正的回滚不会由 Agent 工具自主执行。** 回滚会改变当前会话上下文，并可能触发 QwenPaw 重新加载会话状态，因此必须由用户通过斜杠命令执行：

```text
/pawgit rewind <target>
/pawgit rewind <target> --include-memory --confirm
```

Agent 工具负责预览；需要真正回滚时，请提示用户运行上述命令。

---

## 时间线

```text
/pawgit timeline
/pawgit timeline --limit=10
/pawgit timeline --all
```

- 默认只显示**当前会话**的检查点。
- `--limit=N` 限制每个分区的行数，上限为 `timeline.max_limit`。
- `--all` 显示工作区内所有会话的检查点（供参考；回滚仍只针对当前会话）。
- 输出包含轻量 ASCII DAG：`*` = 当前 HEAD，`o` = 活跃路径，`x` = 因回滚产生的分支。
- 条目按检查点类型（auto / snapshot / pre-rewind）和频道分组。
- 回滚命令仅对**当前会话**的检查点显示；其他会话条目仅作上下文，不要用其序号回滚。

每行包含：类型、快照名、SHA 前缀、时间戳、频道、用户 query 预览、可用回滚命令。

---

## 快照

```text
/pawgit snapshot
/pawgit snapshot before-refactor
/pawgit snapshot Release candidate 1
```

- 在 `refs/snap/` 下创建**永久**快照。
- 消息同时用作标签和 commit 正文；未提供时自动生成基于时间戳的标签。
- 标签会清理为安全的 Git ref 组件；重名时自动加数字后缀。
- `snapshot` ref **永远不会**被 GC 删除。

每个检查点还会写入 `PawGit-Metadata`（最新用户 query、频道、用户 ID、会话 ID、逻辑 parent 等）。时间线用这些元数据拼 DAG，并展示单行 query 预览（支持中文）。

---

## 回滚

目标可以是：

- 时间线索引，如 `1`
- 快照名，如 `before-refactor`
- commit SHA 或唯一前缀
- 完整 PawGit ref

```text
/pawgit rewind 1
/pawgit rewind before-refactor
/pawgit rewind a1b2c3d4
/pawgit rewind 1 --dry-run
```

**普通回滚**只还原当前会话的 JSON，不修改 `MEMORY.md`、`memory/`、其他会话或任意工作区文件。

真正回滚前会创建 `pre-rewind` 安全检查点。`--dry-run` 只解析目标并展示将还原的内容，不写文件、不创建备份。

若纯数字字符串同时可能是快照名和时间线索引，PawGit **优先按时间线索引**解析；要定位数字快照名请用完整 ref 或 SHA。

---

## 记忆回滚

```text
/pawgit rewind before-refactor --include-memory --dry-run
/pawgit rewind before-refactor --include-memory --confirm
```

记忆回滚会还原：

1. 当前会话 JSON
2. 工作区根目录的 `MEMORY.md`
3. 目标快照中 `memory/` 下存在过的文件

范围比普通会话回滚更广。`MEMORY.md` 和 `memory/` 在 workspace 内**跨频道、跨会话共享**，其他会话在目标点之后对记忆的修改可能被丢弃。

安全行为：

- 非试运行的记忆回滚需要 `--confirm`
- 先创建 `pre-rewind` 备份；失败时尝试从备份恢复
- 维护期间通过 `query_gate` 阻塞新的 Agent 回复

---

## 垃圾回收

```text
/pawgit gc
/pawgit gc --dry-run
/pawgit gc --all-sessions
/pawgit gc --compact
```

- 默认只处理**当前会话**的 `auto` 和 `pre-rewind` ref。
- `--all-sessions` 扩展到工作区所有会话。
- `--dry-run` 只报告将删/留的 ref。
- `--compact` 删除范围内全部可回收的 `auto` 和 `pre-rewind` ref。
- **`snapshot` ref 永不删除。**
- 非试运行 GC 在删 ref 后执行 `git gc --prune=now`。

自动检查点也会按保留策略触发 GC。策略同时看**数量**和**时间**：

- 每会话保留最新 `gc.gc_keep_count` 个 auto 检查点
- 保留 `gc.gc_keep_days` 内的 auto 检查点
- 保留 `gc.pre_rewind_retention_days` 内的 pre-rewind ref

---

## 重置

```text
/pawgit reset
/pawgit reset --confirm
```

`reset` 删除并重建当前工作区的 `.pawgit/`（影子仓库、ref、DAG HEAD、`config.toml` 等）。

**不会修改：** 用户项目文件、会话 JSON、`MEMORY.md`、`memory/`。

不带 `--confirm` 时只显示风险提示。

---

## 快照内容与边界

每个检查点是对工作区的**全量盘点**（无父 Git commit + 完整 tree）。PawGit 每次从零重建 index 并用 `git add -f`，因此工作区 `.gitignore`（含 `coding_projects/` 下嵌套仓库生成的规则）**不能**改变快照边界；排除项仅由 PawGit 自己的规则决定。

默认排除：

```text
.git/
.pawgit/
coding_projects/
file_store/
.reme_store_*/
backup/
browser/
__pycache__/
*.pyc
*.log
```

完整列表见 `core.support.EXCLUDE_PATTERNS`。排除 `coding_projects/` 可避免嵌套 Git 仓库或未检出提交导致快照失败。

---

## 存储布局

```text
<workspace>/.pawgit/
|-- shadow.git/    # 私有裸 Git 仓库（存无父 commit 与 blob）
|-- index          # 私有 Git index（每次快照前清空重建）
|-- config.toml    # 工作区级 PawGit 配置
|-- heads.json     # 各 session 的逻辑 HEAD（DAG 尖端）
```

Ref 类别：

| Ref 前缀 | 含义 | GC |
| --- | --- | --- |
| `refs/auto/` | Agent 回复后的自动检查点 | 按数量/时间/compact 清理 |
| `refs/snap/` | 用户永久快照 | 始终保留 |
| `refs/pre-rewind/` | 真正回滚前的安全备份 | 按保留天数/compact 清理 |

ref 删除后，Git 对象可能仍暂留，直到非试运行 GC 执行 `git gc --prune=now`。

---

## 配置

路径：`<workspace>/.pawgit/config.toml`

PawGit 会在文件变更时自动 reload，无需重装插件。

默认配置：

```toml
[gc]
gc_keep_count = 20
gc_keep_days = 7
pre_rewind_retention_days = 7

[auto]
debounce_seconds = 1.5

[timeline]
default_limit = 20
max_limit = 200

[display]
query_preview_chars = 120

[safety]
include_memory_quiesce_timeout = 30.0
```

| 配置键 | 说明 |
| --- | --- |
| `gc.gc_keep_count` | 每会话保留的最少最新 auto 检查点数 |
| `gc.gc_keep_days` | auto 检查点时间窗口（天） |
| `gc.pre_rewind_retention_days` | pre-rewind 保留窗口（天） |
| `auto.debounce_seconds` | 自动快照防抖间隔（秒） |
| `timeline.default_limit` | 默认时间线行数 |
| `timeline.max_limit` | 时间线行数上限 |
| `display.query_preview_chars` | 时间线 query 预览最大长度 |
| `safety.include_memory_quiesce_timeout` | 记忆回滚前等待任务静止的超时（秒） |

命令行参数（如 `--limit=5`）只影响当次调用，不会写回 `config.toml`。

---

## 模块映射

| 文件 | 职责 |
| --- | --- |
| `backend.py` | 插件入口；注册斜杠命令、Agent 工具、Skill、钩子 |
| `handlers.py` | `/pawgit` 帮助与子命令分发 |
| `tools.py` | Agent 可调用的 `pawgit` 工具 |
| `registry.py` | 按工作区的引擎注册与自动快照防抖 |
| `core/engine.py` | 快照、时间线、回滚、GC、重置编排 |
| `core/memory_rewind.py` | 记忆回滚维护门与文件还原 |
| `core/repository.py` | 影子 Git、配置、Git 可用性、原子写入 |
| `core/support.py` | 数据模型、排除规则、元数据、渲染 |
| `core/utils.py` | 会话路径、ref 清理、命令解析 |
| `skills/pawgit/SKILL.md` | 指导 Agent 何时如何使用 PawGit 工具 |
