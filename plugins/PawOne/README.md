# PawOne

PawOne is a QwenPaw slash-command control plane. It lets one conversation
open another agent conversation and send commands to it without modifying
QwenPaw core code.

## Commands

| Command | Description |
|---|---|
| `/pawone ls --agents` | List configured agents. |
| `/pawone ls [agent_id] [--channel=name] [--limit=N]` | List recent chats. |
| `/pawone use <agent_id> <chat_id\|N> [--channel=name]` | Bind this conversation to a target chat. |
| `/pawone <message>` | Send a message to the bound chat and wait for the reply. |
| `/pawone run <message>` | Submit a background task to the bound chat. |
| `/pawone status` | Show the last submitted task status, or chat status. |
| `/pawone stop` | Request stop for the bound target chat. |
| `/pawone close` | Clear this conversation's binding. |
| `/pawone --help` | Show command help. |

## Example

```text
/pawone ls --agents
/pawone ls coding-agent --channel=console
/pawone use coding-agent 1
/pawone Please inspect the current repository status.
/pawone run Run the long test suite and report back.
/pawone status
```

The old verbose commands still work as compatibility aliases: `agents`,
`list`, `open`, `current`, `send`, and `submit`.

## Scope

This phase is intentionally open by default because QwenPaw is usually
deployed as a private personal runtime. Permission filters such as
`enabled_channels`, `allowed_users`, and `allowed_agents` can be added later
without changing the command model.

PawOne does not transparently reroute normal non-slash messages. That would
require changes to QwenPaw's core message dispatch path. Instead, all remote
control is explicit through `/pawone <message>` or `/pawone run <message>`.

When `/pawone <message>` receives the target agent's reply, PawOne also tries
to proactively echo that reply into the target channel session. This uses the
channel's normal proactive send path, so it depends on the target channel
being available and, for chat platforms such as DingTalk, having a usable
recent webhook or API fallback.
