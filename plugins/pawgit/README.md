# PawGit

[[English](README_en.md)]

PawGit 是一个 QwenPaw 插件，用于对会话上下文和记忆状态进行版本控制。它将检查点存储在 `.pawgit` 下的私有影子 Git 仓库中，然后让用户查看时间线、预览回滚、回滚当前会话、可选地回滚记忆源文件、清理旧引用，或重置 PawGit 状态。

## 功能特性

| 功能 | 说明 |
| --- | --- |
| 自动检查点 | 在 Agent 回复后，PawGit 会为当前会话创建一个去抖动的检查点。 |
| 命名快照 | 用户可以创建永久检查点，PawGit GC 不会将其删除。 |
| 时间线 | 按类型和频道分组展示检查点，并附带 ASCII DAG 预览。 |
| 会话回滚 | 仅还原当前频道/会话的会话状态。 |
| 记忆回滚 | 还原当前会话以及 `MEMORY.md` 和 `memory/`。 |
| Agent 工具 | 让 Agent 创建快照、查看时间线、预览回滚，并执行安全的维护操作。 |
| 垃圾回收 | 根据保留策略清理可回收的 `auto` 和 `pre-rewind` 引用。 |
| 重置 | 删除并重建 `.pawgit`，不影响会话或记忆文件。 |

## 斜杠命令

PawGit 提供一个统一的斜杠命令：

```text
/pawgit timeline [--limit=N] [--all]
/pawgit snapshot [message]
/pawgit rewind <N|snap_name|sha> [--dry-run]
/pawgit rewind <target> --include-memory [--dry-run|--confirm]
/pawgit gc [--compact] [--all-sessions] [--dry-run]
/pawgit reset --confirm
/pawgit --help
```

不带参数运行 `/pawgit` 也会显示帮助信息。

常见流程：

```text
/pawgit timeline
/pawgit snapshot before-refactor
/pawgit rewind before-refactor --dry-run
/pawgit rewind before-refactor
```

## Agent 工具

插件注册了一个名为 `pawgit` 的内置 Agent 工具。

该 Agent 工具适用于：

- 在执行有风险的操作前创建快照
- 渲染时间线
- 预览回滚目标
- 执行 GC 试运行或确认后的 GC
- 在显式确认后重置 PawGit

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

真正的回滚在 Agent 工具中不可自主执行。真正的回滚会改变当前会话上下文，可能需要 QwenPaw 重新加载会话状态，因此用户必须自己通过**斜杠命令**路径执行：

```text
/pawgit rewind <target>
/pawgit rewind <target> --include-memory --confirm
```

Agent 工具可以预览这些操作，然后在需要真正回滚时请用户运行斜杠命令。

## 时间线

```text
/pawgit timeline
/pawgit timeline --limit=10
/pawgit timeline --all
```

- 默认仅显示当前会话的检查点。
- `--limit=N` 限制每个分区的行数，上限为 `timeline.max_limit`。
- `--all` 显示当前工作区中所有会话的检查点。
- 时间线输出包含一个从提交元数据派生的轻量级 ASCII DAG。
- `*` 标记当前会话的 HEAD，`o` 标记活跃路径，`x` 标记因回滚而留下的分支。
- 条目按检查点类型（`auto`、`snapshot`、`pre-rewind`）分组，再频道分组。
- 回滚命令仅对当前会话的检查点显示。其他会话的检查点仅作为上下文展示，不应使用当前会话的时间线索引进行回滚。

每个时间线行包含：

- 检查点类型
- 快照名称
- 提交 SHA 前缀
- 真实时间戳
- 频道
- 最新用户查询预览
- 适用时的回滚命令

## 快照

```text
/pawgit snapshot
/pawgit snapshot before-refactor
/pawgit snapshot Release candidate 1
```

- 在 `refs/snap/` 下创建永久快照。
- 消息同时用作快照标签和提交正文。
- 如果未提供消息，PawGit 会生成基于时间戳的标签。
- 标签会被清理为安全的 Git ref 组件。
- 重复的标签会附加数字后缀。
- 命名快照永远不会被 PawGit GC 删除。

每个检查点都存储提交元数据，包含最新已持久化的用户查询、频道、用户 ID、会话 ID 和父提交。时间线渲染会显示安全的单行查询预览，并正确保留中文文本。

## 回滚

目标可以是：

- 时间线索引，例如 `1`
- 快照名称，例如 `before-refactor`
- 提交 SHA 或唯一前缀
- 完整的 PawGit ref

```text
/pawgit rewind 1
/pawgit rewind before-refactor
/pawgit rewind a1b2c3d4
/pawgit rewind 1 --dry-run
```

普通回滚仅还原当前会话的 JSON。它不会修改 `MEMORY.md`、`memory/`、其他会话或任意工作区文件。

在真正回滚之前，PawGit 会创建一个 `pre-rewind` 检查点作为安全备份。`--dry-run` 仅解析目标并展示将要还原的内容；它不会写入文件或创建安全检查点。

如果一个纯数字字符串同时是快照名称和可能的时间线索引，PawGit 会优先将其视为时间线索引。请使用完整的 ref 或 SHA 来直接定位该数字快照。

## 记忆回滚

```text
/pawgit rewind before-refactor --include-memory --dry-run
/pawgit rewind before-refactor --include-memory --confirm
```

记忆回滚会还原：

1. 当前会话 JSON
2. 工作区根目录下的 `MEMORY.md`
3. 目标检查点中 `memory/` 下存在过的文件

这比普通的会话回滚范围更广。`MEMORY.md` 和 `memory/` 在当前 workspace 所有频道和会话之间共享，因此其他会话在目标检查点之后对记忆的修改可能会被丢弃。

安全行为：

- 非试运行的记忆回滚需要 `--confirm`
- PawGit 会先创建一个 `pre-rewind` 安全检查点
- 如果还原失败，PawGit 会尝试从安全检查点恢复
- 正在进行的查询会在记忆回滚维护门后等待

## 垃圾回收

```text
/pawgit gc
/pawgit gc --dry-run
/pawgit gc --all-sessions
/pawgit gc --compact
```

- 默认情况下，GC 仅处理当前会话的 `auto` 和 `pre-rewind` 引用。
- `--all-sessions` 将清理范围扩展到工作区中的所有会话。
- `--dry-run` 报告将被删除或保留的引用。
- `--compact` 删除范围内所有可回收的 `auto` 和 `pre-rewind` 引用。
- `snapshot` 引用永远不会被 PawGit GC 删除。
- 非试运行的 GC 在删除引用后会执行 `git gc --prune=now`。

自动检查点也会按照默认保留策略触发 GC。

保留策略同时使用数量和时间窗口：

- 每个会话保留最新的 `gc.gc_keep_count` 个 auto 检查点
- 保留比 `gc.gc_keep_days` 更新的 auto 检查点
- 保留比 `gc.pre_rewind_retention_days` 更新的 `pre-rewind` 引用

## 重置

```text
/pawgit reset
/pawgit reset --confirm
```

`reset` 会删除并重建当前工作区的 `.pawgit` 目录，包括影子 Git 仓库、引用、DAG HEAD 元数据和 PawGit 配置。

它不会修改：

- 用户项目文件
- 会话 JSON 文件
- `MEMORY.md`
- `memory/`

不带 `--confirm` 运行 `/pawgit reset` 仅显示风险提示信息。

## 快照内容

每个检查点都是 PawGit 影子 Git 仓库中的一个根提交。PawGit 会为每个快照从零重建 Git 索引，并使用 `git add -f`，因此工作区的 `.gitignore` 文件无法改变快照边界。

默认排除项包括：

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

实际列表由 `support.EXCLUDE_PATTERNS` 定义。排除 `coding_projects/` 可以避免因嵌套仓库或未检出提交的项目而导致的失败。

## 存储布局

```text
<workspace>/.pawgit/
|-- shadow.git/    # 私有裸 Git 仓库
|-- index          # 私有 Git 索引
|-- config.toml    # 工作区级别的 PawGit 配置
|-- heads.json     # 每个会话的当前 DAG HEAD
```

引用类别：

| Ref 前缀 | 含义 | GC 行为 |
| --- | --- | --- |
| `refs/auto/` | Agent 回复后的自动检查点 | 按数量/时间/compact 清理 |
| `refs/snap/` | 用户创建的永久快照 | 始终保留 |
| `refs/pre-rewind/` | 真正回滚前的安全检查点 | 按保留天数/compact 清理 |

在引用被删除后，Git 对象可能仍然保留，直到非试运行的 GC 对其进行清理。

## 配置

配置文件：

```text
<workspace>/.pawgit/config.toml
```

PawGit 会在文件变更时重新加载配置，因此用户无需重新安装插件即可编辑配置。

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
| `gc.gc_keep_count` | 每个会话保留的最少最新 auto 检查点数量。 |
| `gc.gc_keep_days` | 保留 auto 检查点的时间窗口。 |
| `gc.pre_rewind_retention_days` | pre-rewind 安全引用的保留窗口。 |
| `auto.debounce_seconds` | 创建自动检查点前的去抖动延迟。 |
| `timeline.default_limit` | 默认时间线行数限制。 |
| `timeline.max_limit` | 可接受的最大时间线行数限制。 |
| `display.query_preview_chars` | 时间线中查询预览的最大长度。 |
| `safety.include_memory_quiesce_timeout` | 等待记忆回滚静止的最大秒数。 |

命令行参数仅影响当前调用。例如，`/pawgit timeline --limit=5` 不会修改 `config.toml`。

## 模块映射

| 文件 | 职责 |
| --- | --- |
| `backend.py` | 插件入口点；注册斜杠命令、Agent 工具、Skill 提供者和钩子。 |
| `handlers.py` | `/pawgit` 帮助文本和子命令分发。 |
| `tools.py` | Agent 可调用的 PawGit 工具，用于快照、时间线、回滚预览、GC 和重置。 |
| `registry.py` | 按工作区的引擎注册表和自动快照去抖动。 |
| `engine.py` | 快照、时间线、会话回滚、GC 和重置的编排。 |
| `memory_rewind.py` | 记忆回滚维护门和文件还原。 |
| `repository.py` | 影子 Git 设置、配置加载、Git 可用性检查和原子写入。 |
| `support.py` | 数据模型、排除项、元数据辅助、目标解析和渲染器。 |
| `utils.py` | 会话路径、ref 清理、会话键和命令解析辅助。 |
| `skills/pawgit/SKILL.md` | 教导 Agent 何时以及如何使用 PawGit 工具的 Skill 指令。 |
