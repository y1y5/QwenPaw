# PawOne

PawOne 是一个 QwenPaw slash command 插件，用来在一个会话里控制其它
agent、其它 channel、其它 session。

典型场景：

- 在手机钉钉里控制本机 console 会话。
- 在一个 agent 会话里给另一个 agent 派发任务。
- 在当前 channel 中查看、绑定、停止其它 session。

PawOne 不修改 QwenPaw 核心代码。它只通过插件命令和 QwenPaw 已有的
multi-agent / channel 能力工作。

## 命令

| 命令 | 说明 |
|---|---|
| `/pawone ls --agents` | 查看当前配置中的 agents。 |
| `/pawone ls [agent_id] [--channel=name] [--limit=N]` | 查看某个 agent 最近的会话。 |
| `/pawone use <agent_id> <chat_id\|N> [--channel=name]` | 将当前会话绑定到一个目标会话。 |
| `/pawone <message>` | 向已绑定的目标会话发送消息，并等待回复。 |
| `/pawone run <message>` | 向已绑定的目标会话提交后台任务。 |
| `/pawone status` | 查看最近后台任务状态；如果没有后台任务，则查看目标会话状态。 |
| `/pawone stop` | 请求停止目标会话当前正在运行的任务。 |
| `/pawone close` | 清除当前会话的 PawOne 绑定。 |
| `/pawone --help` | 查看帮助。 |

旧命令仍然兼容：

```text
agents, list, open, current, send, submit
```

也就是说，下面两种写法都可以：

```text
/pawone send 帮我检查仓库状态
/pawone 帮我检查仓库状态
```

推荐使用更短的第二种。

## 使用示例

先查看有哪些 agent：

```text
/pawone ls --agents
```

查看 `coding-agent` 的 console 会话：

```text
/pawone ls coding-agent --channel=console
```

绑定列表中的第 1 个会话：

```text
/pawone use coding-agent 1
```

绑定后，直接发送消息：

```text
/pawone 请检查当前仓库状态
```

提交后台任务：

```text
/pawone run 跑一下完整测试，然后把结果告诉我
```

查看状态：

```text
/pawone status
```

停止目标任务：

```text
/pawone stop
```

取消绑定：

```text
/pawone close
```

## 绑定模型

PawOne 的绑定是按“发起会话”区分的。

也就是说，不同 agent、不同 channel、不同 user、不同 session 之间的绑定
互不影响。

例如：

- console 会话 A 可以绑定到 dingding 会话 X。
- dingding 会话 B 可以绑定到 console 会话 Y。
- 两个绑定会分别保存，不会互相覆盖。

当前绑定只保存在 QwenPaw 当前运行进程内。应用重启后，需要重新
`/pawone use ...`。

## 回复回显

当执行：

```text
/pawone <message>
```

PawOne 会做两件事：

1. 把消息发送给目标 agent/session。
2. 收到目标 agent 回复后，尝试把回复主动回显到目标 channel/session。

这里的“回显”是 best-effort，不是强保证。

PawOne 使用的是 QwenPaw channel 的通用主动发送接口：

```text
channel.to_handle_from_target(...)
channel.send(...)
```

因此它不是只支持 console 和 DingTalk。只要某个 channel 自己支持主动发送，
并且能够通过 `session_id`、`user_id` 或保存的 `meta` 找回目标，PawOne 就
可以工作。

大致可以分为三类：

| 类型 | 说明 |
|---|---|
| 基本可靠 | console、telegram、discord、mattermost、mqtt 等。 |
| 依赖上下文 | dingtalk、feishu、wechat、wecom 等，通常依赖最近消息保存下来的 webhook、receive_id、conversation_id 等。 |
| 不保证 | voice、sip 等实时语音或通话类 channel。 |

如果目标 channel 的 webhook 过期、缓存缺失、API 不可用，控制端仍然可以
收到 `/pawone <message>` 的返回结果，但目标 channel 可能收不到主动回显。

## 权限

当前版本默认开放。

这是因为 QwenPaw 通常是用户自己的私有化运行环境，所以暂时没有强制限制：

```text
enabled_channels
allowed_users
allowed_agents
```

后续如果要用于多人共享部署，可以在 PawOne 外层增加 allowlist 配置。

## 边界

PawOne 不会透明接管普通非 slash 消息。

也就是说，下面这种普通消息不会自动转发到目标会话：

```text
帮我检查仓库状态
```

必须显式使用：

```text
/pawone 帮我检查仓库状态
```

原因是透明转发需要修改 QwenPaw 核心消息分发链路，而 PawOne 当前保持为
纯插件实现，不修改核心代码。

