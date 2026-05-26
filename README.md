# Head-Nurse

A GitHub Project V2 kanban agent that bridges your phone to Claude Code on your Mac.

## How it works

```
Phone (GitHub Mobile)  ←→  GitHub Project V2 Board  ←→  Mac Agent  →  Claude Code
```

- Create an Issue → Agent picks it up and runs Claude Code
- Agent posts results as comments with status updates
- Reply with comments for multi-turn conversations
- Board columns: Inbox → In Progress → Waiting → Completed / Failed

## Setup

```bash
chmod +x setup.sh
./setup.sh
```

## Configuration

`~/.config/kanban-agent/config.yaml`:

```yaml
repo: "your-username/kanban-tasks"
project_number: 2
poll_interval_seconds: 30
claude_command: "claude"
claude_working_dir: "~/Projects"
claude_permission_mode: "bypassPermissions"
task_timeout_seconds: 600
max_budget_per_task_usd: 1.0
```

## Run manually

```bash
python -m kanban_agent
```

## Architecture

| Module | Role |
|--------|------|
| `agent.py` | Polling loop, task lifecycle, state persistence |
| `project_board.py` | GitHub Project V2 GraphQL operations |
| `executor.py` | Claude Code subprocess management |
| `github.py` | `gh` CLI async wrapper with retry |
| `models.py` | Data classes (Task, TaskStatus) |
| `config.py` | YAML config loading |

## Features

- **Multi-turn conversations** — same Claude session via `--resume`
- **Waiting state** — detects when Claude asks a question, pauses until you reply
- **Comment editing** — "executing..." placeholder gets replaced with final result
- **Crash recovery** — state persisted to JSON, launchd auto-restarts
