# PawGit

PawGit 为 QwenPaw workspace 提供独立的影子 Git 检查点、时间线、会话回滚和记忆回滚。

## 功能概览


| 功能    | 说明                                       |
| ----- | ---------------------------------------- |
| 自动检查点 | Agent 回复后，按防抖时间创建 workspace 快照           |
| 永久快照  | 用户手动创建并命名；不会被 PawGit GC 删除               |
| 时间线   | 按 `auto`、`snapshot`、`pre-rewind` 分类展示检查点 |
| 会话回滚  | 只恢复当前 channel 和 session 的会话 JSON         |
| 记忆回滚  | 同时恢复当前会话、`MEMORY.md` 和 `memory/`         |
| 垃圾回收  | 自动（主动）清理满足策略的自动检查点和回滚前备份                 |


## 命令

PawGit 注册一个顶级命令：`/pawgit`。

```text
/pawgit timeline [--limit=N] [--all]
/pawgit snapshot [message]
/pawgit rewind <N|snap_name|sha> [--dry-run]
/pawgit rewind <target> --include-memory [--dry-run|--confirm]
/pawgit gc [--compact] [--all-sessions] [--dry-run]
/pawgit --help
```

不带参数执行 `/pawgit` 也会显示帮助信息。

常用流程：

```text
/pawgit timeline
/pawgit snapshot before-refactor
/pawgit rewind before-refactor --dry-run
/pawgit rewind before-refactor
```

### Timeline

```text
/pawgit timeline
/pawgit timeline --limit=10
/pawgit timeline --all
```

- 默认只显示当前 session 的检查点。
- `--limit=N` 限制返回条数，并受配置中的 `max_limit` 约束。
- `--all` 显示当前 workspace 内所有 session 的检查点。
- 输出顶部根据 commit metadata 中的 parent 关系绘制轻量 ASCII DAG；
  `*` 表示当前 HEAD，`o` 表示 active path，`x` 表示 branch。
- 输出先按 `auto`、`snapshot`、`pre-rewind` 分类，再按 channel 分组。
- 当前 session 的每一行都会给出可直接执行的 `/pawgit rewind ...` 命令。
- `--all` 输出中，只有当前 session 的检查点会显示 rewind way。其他
channel 或 session 的检查点只用于查看，不能从当前 session 直接 rewind。

### Snapshot

```text
/pawgit snapshot
/pawgit snapshot before-refactor
/pawgit snapshot Release candidate 1
```

- 创建永久快照，存储在 `refs/snap/`。
- message 同时用于快照名称和提交说明。
- 未提供 message 时，PawGit 自动生成名称。
- 名称会被转换为安全的 Git ref；重名时自动追加数字后缀。
- 永久快照不会被 `/pawgit gc` 删除。

### Rewind

目标可以是：

- `/pawgit timeline` 中的序号，例如 `1`
- snapshot 名称，例如 `before-refactor`
- commit SHA 或其唯一前缀
- 完整 PawGit ref

```text
/pawgit rewind 1
/pawgit rewind before-refactor
/pawgit rewind a1b2c3d4
/pawgit rewind 1 --dry-run
```

普通 rewind 只恢复当前  session 对应的会话 JSON，不修改
`MEMORY.md`、`memory/` 或其他 session。

执行真实回滚前，PawGit 会创建一个 `pre-rewind` 安全检查点。`--dry-run`
只解析目标并展示将恢复的文件，不写入 workspace，也不创建安全检查点。

如果纯数字同时也是一个 snapshot 名称，PawGit 优先按 snapshot 名称解析，
而不是按时间线序号解析。

### Memory Rewind

```text
/pawgit rewind before-refactor --include-memory --dry-run
/pawgit rewind before-refactor --include-memory --confirm
```

Memory rewind 恢复：

1. 当前 session 的会话 JSON
2. workspace 根目录下的 `MEMORY.md`
3. `memory/` 目录及其在目标检查点中的文件

这是 workspace 级操作。`MEMORY.md` 和 `memory/` 由所有 channel 和 session
共享，因此目标检查点之后由其他会话写入的记忆也会被丢弃。

保护措施：

- 非 dry-run 操作必须显式提供 `--confirm`。
- 回滚前创建 `pre-rewind` 安全检查点。
- 文件恢复失败时，PawGit 尝试从安全检查点恢复原状态。

### Garbage Collection

```text
/pawgit gc
/pawgit gc --dry-run
/pawgit gc --all-sessions
/pawgit gc --compact
```

- 默认只处理当前 session 的 `auto` 和 `pre-rewind` refs。
- `--all-sessions` 将清理范围扩大到整个 workspace。
- `--dry-run` 只显示将删除和保留的 refs。
- `--compact` 删除作用域内全部可回收的 `auto` 和 `pre-rewind` refs。
- `snapshot` refs 永远不会被 PawGit GC 删除。
- 非 dry-run GC 最后会运行 `git gc --prune=now`。

每次自动检查点完成后，PawGit 也会按默认策略自动运行一次 GC。

普通 GC 对自动检查点采用“数量或时间”保留策略：只要检查点属于最新
`gc_keep_count` 个，或者仍在 `gc_keep_days` 天以内，就会保留。
`pre-rewind` refs 单独按 `pre_rewind_retention_days` 清理。

## 快照内容

每个检查点都是一个**无父**提交的完整 workspace 快照。PawGit 每次都会从空索引
重建快照，并使用 `git add -f` 绕过 workspace 内所有 `.gitignore`。

以下内容默认排除：

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

实际列表以 `support.EXCLUDE_PATTERNS` 为准。排除 `coding_projects/` 可以避免
嵌套项目或未初始化子仓库导致 workspace 快照失败。

每个 `auto`、`snapshot` 和 `pre-rewind` 提交都会在 commit message 中保存
`PawGit-Metadata` JSON，其中包含当时最新的已持久化用户 query 和父节点 SHA。
元数据保留完整文本，timeline 只显示经过单行化和截断的预览，并正确保留中文。
`.pawgit/heads.json` 只记录各 session 当前 HEAD，不复制完整节点信息。

## 存储结构

```text
<workspace>/.pawgit/
|-- shadow.git/    # 独立 bare Git 仓库，即 GIT_DIR
|-- index          # 独立 Git index，即 GIT_INDEX_FILE
|-- config.toml    # workspace 级运行配置
|-- heads.json     # 每个 session 的当前 DAG HEAD
```

Ref 分类：


| Ref 前缀             | 内容              | GC 行为              |
| ------------------ | --------------- | ------------------ |
| `refs/auto/`       | Agent 回复后的自动检查点 | 按数量、时间或 compact 清理 |
| `refs/snap/`       | 用户创建的永久快照       | 始终保留               |
| `refs/pre-rewind/` | 每次真实回滚前的安全检查点   | 按保留天数或 compact 清理  |


Git 对象可能在 refs 被删除后暂时保留，直到非 dry-run GC 执行对象清理。

## 配置

配置文件位于 `<workspace>/.pawgit/config.toml`。PawGit 会检测文件修改时间，
后续命令自动使用新配置，无需重新安装插件。

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


| 配置项                                     | 说明                          |
| --------------------------------------- | --------------------------- |
| `gc.gc_keep_count`                      | 每个 session 至少保留的最新自动检查点数量   |
| `gc.gc_keep_days`                       | 自动检查点的时间保留窗口                |
| `gc.pre_rewind_retention_days`          | 回滚前安全检查点的保留天数               |
| `auto.debounce_seconds`                 | Agent 回复后创建自动检查点的防抖时间       |
| `timeline.default_limit`                | timeline 默认返回条数             |
| `timeline.max_limit`                    | timeline 允许的最大返回条数          |
| `display.query_preview_chars`           | timeline 中 query 预览的最大字符数   |
| `safety.include_memory_quiesce_timeout` | Memory rewind 等待其他任务退出的最长秒数 |


1. `gc.gc_keep_count`和`gc.gc_keep_days`可以配合使用，`/pawgit gc`命令会保留
**最新的`gc.gc_keep_count`个检查点 以及 `gc.gc_keep_days`窗口内的检查点**。

2. 命令行参数只影响当前调用。例如，`/pawgit timeline --limit=N` 会覆盖
`timeline.default_limit`，但不会修改 `config.toml`。

## 模块结构


| 文件                 | 职责                                  |
| ------------------ | ----------------------------------- |
| `backend.py`       | 插件入口，注册 `/pawgit` 和 Agent hooks     |
| `handlers.py`      | `/pawgit` 帮助与子命令分发                  |
| `registry.py`      | 每 workspace Engine 注册表和自动快照防抖       |
| `engine.py`        | snapshot、timeline、普通 rewind 和 GC 编排 |
| `memory_rewind.py` | Memory rewind 的任务静默、文件恢复和回滚         |
| `repository.py`    | Shadow Git、配置加载和原子文件写入              |
| `support.py`       | 数据模型、排除策略、元数据、ref 解析和渲染             |
| `utils.py`         | session 路径、ref 清洗和命令参数辅助函数          |


Agent 回复完成后，`post_reply` hook 调度自动检查点；真实 Memory rewind
期间，`pre_reply` hook 通过 `query_gate` 等待维护事务结束。
