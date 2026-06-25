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

This builds `HeadNurse.app`, installs it to `~/Applications`, and migrates any existing launchd setup. Edit `~/.config/kanban-agent/config.yaml` then launch:

```bash
open ~/Applications/HeadNurse.app
```

The app appears in the menu bar (no Dock icon). Click the icon to Start/Stop the agent, open the config, or view logs.

To auto-start at login: System Settings → General → Login Items → drag `HeadNurse.app` into the list.

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

## Run manually (headless)

For debugging or non-GUI use:

```bash
python -m kanban_agent
```

This skips the menubar and runs the agent directly in the foreground.

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

## Calendar Sync (Outlook → CalDAV)

Optionally mirrors your Outlook calendar to a self-hosted CalDAV server (e.g., Radicale on NAS).

**Setup:**

1. Ensure Outlook MCP tokens exist at `~/.sap-mcp/cookies/outlook/sap_tokens.json`
2. Add `calendar_sync` section to `~/.config/kanban-agent/config.yaml`:

```yaml
calendar_sync:
  enabled: true
  caldav_url: "https://your-nas:5232/user/calendar/"
  caldav_username: "user"
  caldav_password: "pass"
```

3. Restart HeadNurse — sync starts automatically every 15 minutes.

**Behavior:**
- One-way sync: Outlook → CalDAV (CalDAV is a read-only mirror)
- Syncs today + 14 days ahead
- Creates, updates, and deletes events to match Outlook
- Token refresh handled automatically
