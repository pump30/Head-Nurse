# Menubar App for Kanban Agent — Design

**Date:** 2026-05-27
**Status:** Approved, ready for implementation plan

## Goal

Convert the Head-Nurse kanban agent from a launchd-managed background daemon into a macOS menubar app that the user can start, stop, and observe from the menu bar. The icon reflects on/off state at a glance.

## Non-goals

- Multi-account / multi-repo support
- A full preferences window (config is edited via text editor)
- Login Items toggle inside the app (user adds via System Settings)
- App signing / notarization (personal use)
- Idle / working / error icon states (intentionally two-state only)

## User Stories

1. *Quick toggle.* I can click the menubar icon and pick "Start" or "Stop" to control the agent without opening a terminal.
2. *Status at a glance.* I can tell from the icon whether the agent is running.
3. *Drill down when needed.* I can open the live config and the log file from the menu.
4. *Auto-resume.* When I log in, the app starts and the agent starts with it.

## Architecture

Single Python process, two threads:

```
┌─────────────────────────────────────────┐
│  HeadNurse.app  (LSUIElement, menubar)  │
│                                          │
│  ┌──────────────────┐  ┌───────────────┐│
│  │  rumps main      │  │ asyncio loop  ││
│  │  (NSApp runloop) │  │ (worker thread)││
│  │  - menu / icon   │─▶│ - KanbanAgent ││
│  │  - user clicks   │  │ - run() coro  ││
│  │  - polls status  │◀─│ - current_status││
│  └──────────────────┘  └───────────────┘│
└─────────────────────────────────────────┘
        │                    │
        ▼                    ▼
   state file          GitHub Project V2
```

- **Main thread** runs the AppKit runloop via `rumps.App`.
- **Worker thread** is created on Start. It owns a fresh `asyncio` event loop and runs `KanbanAgent.run()` to completion.
- **Stop** calls the existing `agent.shutdown()` (sets internal flag → loop exits cleanly), then joins the thread with a 5-second timeout. If the thread is still alive after the timeout, it is left as a daemon thread so app quit is not blocked; the next Start creates a fresh agent + loop.
- **Quit** performs Stop, then `rumps.quit_application()`.

The agent module has no awareness of the UI. The UI polls the agent's read-only status property; nothing flows the other way except shutdown.

## Components

### `src/kanban_agent/menubar.py` (new)

Owns the rumps app and the worker thread.

Responsibilities:
- Construct the menu (status row, Start/Stop, Restart, Open Config, View Logs, Quit)
- Maintain icon state (`circle.fill` vs `circle`, both as templates)
- Spawn / join the worker thread on Start / Stop
- Run a `rumps.Timer(0.5s)` that pulls `agent.current_status` and refreshes the status row + menu item titles
- Detect worker-thread death (crash) → flip to Stopped + send `rumps.notification` once
- Entry point `main()` registered as console script `kanban-menubar`

### `src/kanban_agent/status.py` (new)

A small dataclass plus a thread-safe holder:

```python
@dataclass(frozen=True)
class AgentStatus:
    state: Literal["stopped", "starting", "running", "crashed"]
    current_issue: int | None
    current_phase: str | None  # e.g. "executing", "polling", "waiting"
    error: str | None          # populated only when state == "crashed"
```

`KanbanAgent` exposes `current_status: AgentStatus` (read-only property). Updates happen inside the agent at lifecycle transitions. The holder uses a `threading.Lock` for assignment so the UI thread always reads a consistent snapshot.

### `src/kanban_agent/agent.py` (modified)

Add status writes at lifecycle points:
- entering polling cycle → `phase="polling"`
- starting work on issue N → `current_issue=N, phase="executing"`
- finishing → `current_issue=None, phase="polling"`
- shutdown begin → `state="stopped"`

No behavior change beyond status updates.

### `src/kanban_agent/__main__.py` (modified)

Keep CLI entry point (`python -m kanban_agent`) for headless / debugging use. The menubar app is the new default but the CLI path is preserved.

### `setup_app.py` (new)

`py2app` configuration:
- App name `HeadNurse`
- Bundle id `com.kanban-agent.menubar`
- `LSUIElement = True` (no Dock icon, menubar only)
- Entry point `kanban_agent.menubar:main`
- Includes `rumps`, `pyyaml`, package data
- Output: `dist/HeadNurse.app`

### `setup.sh` (modified)

Replace launchd install with:
1. `pip install -e .` (already present)
2. `python setup_app.py py2app` to build
3. `cp -R dist/HeadNurse.app ~/Applications/`
4. Generate `~/.config/kanban-agent/config.yaml` from template if missing
5. Print: "Open System Settings → General → Login Items, drag HeadNurse to Open at Login."

Remove: pyyaml install kludge (handled by `pip install -e .`).

### `launchd/com.kanban-agent.plist` (deleted)

The directory `launchd/` is removed.

## Data Flow

### Start
1. User clicks "Start Agent"
2. UI immediately sets icon=`circle.fill`, status row="● Starting…"
3. UI creates `threading.Thread(target=_run_agent, daemon=True).start()`
4. Worker calls `Config.load()` → `KanbanAgent(config)` → `loop.run_until_complete(agent.run())`
5. Agent updates `current_status` to `state="running"` once polling loop begins
6. UI's 0.5s timer picks up the new status, refreshes status row to "● Running · idle"

### Stop
1. User clicks "Stop Agent"
2. UI calls `agent.shutdown()` (existing method, signals via `asyncio.Event`)
3. UI calls `thread.join(timeout=5)`
4. Agent's run loop returns; thread exits; worker loop is closed
5. UI clears reference, sets icon=`circle`, status row="○ Stopped"

### Crash detection
- Every 0.5s timer tick, if `state == "running"` but `thread.is_alive() == False`:
  - Flip to Stopped icon
  - Status row: "○ Crashed · see logs"
  - Send one-shot `rumps.notification("Kanban agent crashed", "...", "Click View Logs.")`
  - Reset internal references so next Start creates a fresh agent

### Config missing
- On Start, if `Config.load()` raises FileNotFoundError:
  - Status row: "○ No config"
  - Notification: "Config not found. Open Config… to create one."
  - Do not spawn worker thread.

## Menu Behavior

```
● Running · #42 executing       (disabled, status row)
─────────────────────────────
Stop Agent                       (toggles to "Start Agent" when stopped)
Restart Agent                    (disabled when stopped)
─────────────────────────────
Open Config…                     (opens ~/.config/kanban-agent/config.yaml)
View Logs…                       (opens ~/Library/Logs/kanban-agent-stdout.log)
─────────────────────────────
Quit
```

Status row formats:
- `○ Stopped`
- `○ No config`
- `○ Crashed · see logs`
- `● Starting…`
- `● Running · idle`
- `● Running · #<issue> <phase>`

Open Config / View Logs use `subprocess.run(["open", path])`. If the log file doesn't exist yet, the directory is opened instead.

## Icons

Two SF Symbol images shipped as PNG inside the app bundle. Both flagged as templates (`isTemplate=True`) so macOS handles light/dark mode automatically.

| State | Symbol | File |
|-------|--------|------|
| Running | `circle.fill` | `resources/icon-on.png` |
| Stopped | `circle` | `resources/icon-off.png` |

Decision: render once at build time using a small `scripts/render_icons.py` so the runtime app has no PyObjC dependency beyond what rumps already pulls in.

## Configuration

No new config keys. `~/.config/kanban-agent/config.yaml` continues to drive the agent. Two implicit UI behaviors:
- The app always launches the agent on first run (auto_start = true, hard-coded)
- The 0.5s status poll interval is hard-coded

If either becomes user-relevant later, add config keys then. YAGNI for now.

## Testing

Unit tests (`pytest`):
- `status.py`: `AgentStatus` formatting → menu strings (table-driven, all 6 cases above)
- Status holder thread-safety (concurrent writes, reader sees consistent snapshot)

Manual test plan (documented in spec, executed before merge):
1. Build `.app`, copy to `~/Applications`, launch
2. Verify menubar icon appears, status="● Running · idle" within 2s
3. Create a test issue → status updates to `#N executing` → completes → returns to idle
4. Click Stop → icon flips, status="○ Stopped" within 1s
5. Click Start → comes back online
6. Click Restart → goes through Stopped → Running once
7. Edit config to invalid YAML → Restart → status="○ Crashed · see logs", notification fires
8. Delete config → Restart → status="○ No config", notification fires
9. Quit → app disappears, no orphan Python process (`pgrep -f kanban_agent` empty)

## Migration

For existing users with `com.kanban-agent` plist loaded:
1. `setup.sh` runs `launchctl unload ~/Library/LaunchAgents/com.kanban-agent.plist 2>/dev/null` if present
2. Removes `~/Library/LaunchAgents/com.kanban-agent.plist`
3. Builds and installs `HeadNurse.app`
4. Prints Login Items instructions

## File Changes Summary

**New:**
- `src/kanban_agent/menubar.py`
- `src/kanban_agent/status.py`
- `setup_app.py`
- `scripts/render_icons.py`
- `resources/icon-on.png`, `resources/icon-off.png`
- `tests/test_status.py`
- `docs/superpowers/specs/2026-05-27-menubar-app-design.md` (this file)

**Modified:**
- `src/kanban_agent/agent.py` (+status writes)
- `src/kanban_agent/__main__.py` (no functional change, doc only)
- `pyproject.toml` (+rumps, +py2app dev, +`kanban-menubar` script)
- `setup.sh` (replace launchd install with py2app build + cp)
- `README.md` (rewrite Setup section)

**Deleted:**
- `launchd/com.kanban-agent.plist`
- `launchd/` (directory)

## Open Questions

None at design time. Implementation may surface platform quirks (py2app + asyncio interaction, sleep/wake handling) which will be addressed in the implementation plan.
