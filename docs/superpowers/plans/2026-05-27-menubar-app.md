# Menubar App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the Head-Nurse kanban agent from a launchd-managed daemon into a macOS menubar app (HeadNurse.app) that hosts the agent in a worker thread, with two-state on/off icon, status row, and Start/Stop/Restart/Open Config/View Logs/Quit menu.

**Architecture:** Single Python process. Main thread runs `rumps.App` (NSApp runloop). Worker thread is created on Start and runs a fresh `asyncio` event loop driving the existing `KanbanAgent.run()`. UI polls a thread-safe `AgentStatus` snapshot every 0.5s to refresh menu text and detect crashes.

**Tech Stack:** Python 3.11+, rumps (menubar), asyncio (existing agent loop), threading (worker), py2app (bundling), pytest (tests). Icons rendered as PNG once at build time via PyObjC `NSImage(systemSymbolName:)`.

---

## File Structure

**New files:**
- `src/kanban_agent/status.py` — `AgentStatus` dataclass + thread-safe holder + menu-text formatter
- `src/kanban_agent/menubar.py` — rumps app: menu, icon, worker-thread lifecycle, status polling
- `setup_app.py` — py2app config
- `scripts/render_icons.py` — one-shot script to render two PNGs from SF Symbols
- `resources/icon-on.png`, `resources/icon-off.png` — generated, committed to repo
- `tests/test_status.py` — unit tests for status formatting + holder concurrency
- `tests/__init__.py` — empty, makes tests a package

**Modified files:**
- `src/kanban_agent/agent.py` — populate `current_status` at lifecycle transitions
- `pyproject.toml` — add `rumps`, dev-extras for `py2app` + `pytest`, add `kanban-menubar` script
- `setup.sh` — replace launchd install with py2app build + cp; unload old plist if present
- `README.md` — rewrite Setup section for the .app

**Deleted:**
- `launchd/com.kanban-agent.plist`
- `launchd/` (directory)

Each file has one responsibility. `status.py` is pure data/formatting (no threading semantics beyond the lock). `menubar.py` is pure UI/threading (no business logic). `agent.py` keeps its existing role and only gains status-write side effects.

---

## Task 1: AgentStatus dataclass and holder

**Files:**
- Create: `src/kanban_agent/status.py`
- Create: `tests/__init__.py`
- Create: `tests/test_status.py`

- [ ] **Step 1: Create empty tests package**

```bash
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 2: Write failing test for AgentStatus.menu_text**

Create `tests/test_status.py`:

```python
import threading
import pytest
from kanban_agent.status import AgentStatus, StatusHolder


class TestMenuText:
    def test_stopped(self):
        s = AgentStatus(state="stopped")
        assert s.menu_text == "○ Stopped"

    def test_no_config(self):
        s = AgentStatus(state="stopped", error="no_config")
        assert s.menu_text == "○ No config"

    def test_crashed(self):
        s = AgentStatus(state="crashed", error="boom")
        assert s.menu_text == "○ Crashed · see logs"

    def test_starting(self):
        s = AgentStatus(state="starting")
        assert s.menu_text == "● Starting…"

    def test_running_idle(self):
        s = AgentStatus(state="running", current_phase="polling")
        assert s.menu_text == "● Running · idle"

    def test_running_with_issue(self):
        s = AgentStatus(state="running", current_issue=42, current_phase="executing")
        assert s.menu_text == "● Running · #42 executing"


class TestStatusHolder:
    def test_default_is_stopped(self):
        h = StatusHolder()
        assert h.get().state == "stopped"

    def test_set_then_get(self):
        h = StatusHolder()
        h.set(AgentStatus(state="running", current_issue=7, current_phase="executing"))
        s = h.get()
        assert s.state == "running"
        assert s.current_issue == 7

    def test_concurrent_writes_consistent_reads(self):
        h = StatusHolder()
        stop = threading.Event()
        errors = []

        def writer(n):
            try:
                for i in range(500):
                    h.set(AgentStatus(state="running", current_issue=n, current_phase="executing"))
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                while not stop.is_set():
                    s = h.get()
                    # Read should always yield a valid snapshot
                    assert s.state in {"stopped", "running", "crashed", "starting"}
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        readers = [threading.Thread(target=reader) for _ in range(2)]
        for t in threads + readers:
            t.start()
        for t in threads:
            t.join()
        stop.set()
        for t in readers:
            t.join()
        assert errors == []


class TestIcon:
    def test_running_uses_on_icon(self):
        s = AgentStatus(state="running")
        assert s.icon_name == "icon-on"

    def test_starting_uses_on_icon(self):
        s = AgentStatus(state="starting")
        assert s.icon_name == "icon-on"

    def test_stopped_uses_off_icon(self):
        s = AgentStatus(state="stopped")
        assert s.icon_name == "icon-off"

    def test_crashed_uses_off_icon(self):
        s = AgentStatus(state="crashed")
        assert s.icon_name == "icon-off"
```

- [ ] **Step 3: Run test, expect ImportError**

Run: `cd /Users/I572881/workspace/Head-Nurse && PYTHONPATH=src python -m pytest tests/test_status.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kanban_agent.status'`

- [ ] **Step 4: Implement status.py**

Create `src/kanban_agent/status.py`:

```python
from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from typing import Literal, Optional

State = Literal["stopped", "starting", "running", "crashed"]


@dataclass(frozen=True)
class AgentStatus:
    state: State = "stopped"
    current_issue: Optional[int] = None
    current_phase: Optional[str] = None  # "polling" | "executing" | "waiting"
    error: Optional[str] = None  # set when state in {"crashed"} or to "no_config"

    @property
    def menu_text(self) -> str:
        if self.state == "stopped":
            if self.error == "no_config":
                return "○ No config"
            return "○ Stopped"
        if self.state == "crashed":
            return "○ Crashed · see logs"
        if self.state == "starting":
            return "● Starting…"
        # running
        if self.current_issue is not None:
            return f"● Running · #{self.current_issue} {self.current_phase or 'executing'}"
        return "● Running · idle"

    @property
    def icon_name(self) -> str:
        return "icon-on" if self.state in ("running", "starting") else "icon-off"


class StatusHolder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = AgentStatus()

    def get(self) -> AgentStatus:
        with self._lock:
            return self._status

    def set(self, status: AgentStatus) -> None:
        with self._lock:
            self._status = status

    def update(self, **changes) -> AgentStatus:
        with self._lock:
            self._status = replace(self._status, **changes)
            return self._status
```

- [ ] **Step 5: Run tests, expect pass**

Run: `cd /Users/I572881/workspace/Head-Nurse && PYTHONPATH=src python -m pytest tests/test_status.py -v`
Expected: 11 passed

- [ ] **Step 6: Commit**

```bash
git add src/kanban_agent/status.py tests/__init__.py tests/test_status.py
git commit -m "feat(status): add AgentStatus dataclass and thread-safe holder"
```

---

## Task 2: Wire status into KanbanAgent

**Files:**
- Modify: `src/kanban_agent/agent.py` — add `_status: StatusHolder`, expose `current_status`, write at lifecycle points

- [ ] **Step 1: Write failing test for KanbanAgent.current_status**

Append to `tests/test_status.py`:

```python
class TestAgentStatus:
    def test_agent_starts_stopped(self, tmp_path, monkeypatch):
        from kanban_agent.config import Config
        from kanban_agent.agent import KanbanAgent

        cfg = Config(
            repo="x/y", project_number=1, poll_interval_seconds=30,
            claude_command="echo", claude_working_dir=str(tmp_path),
            claude_permission_mode="acceptEdits", task_timeout_seconds=10,
            max_budget_per_task_usd=1.0, log_file=str(tmp_path / "log"),
            log_level="INFO", state_file=str(tmp_path / "state.json"),
        )
        agent = KanbanAgent(cfg)
        assert agent.current_status.state == "stopped"

    def test_agent_status_setter_used_by_lifecycle(self, tmp_path):
        from kanban_agent.config import Config
        from kanban_agent.agent import KanbanAgent
        from kanban_agent.status import AgentStatus

        cfg = Config(
            repo="x/y", project_number=1, poll_interval_seconds=30,
            claude_command="echo", claude_working_dir=str(tmp_path),
            claude_permission_mode="acceptEdits", task_timeout_seconds=10,
            max_budget_per_task_usd=1.0, log_file=str(tmp_path / "log"),
            log_level="INFO", state_file=str(tmp_path / "state.json"),
        )
        agent = KanbanAgent(cfg)
        agent._set_status(state="running", current_phase="polling")
        s = agent.current_status
        assert s.state == "running"
        assert s.current_phase == "polling"
```

- [ ] **Step 2: Inspect Config to confirm field list**

Run: `cd /Users/I572881/workspace/Head-Nurse && cat src/kanban_agent/config.py`

Confirm `Config` is a dataclass with the fields used above. If signatures differ, adjust the test fixture inline before continuing.

- [ ] **Step 3: Run test, expect failure**

Run: `cd /Users/I572881/workspace/Head-Nurse && PYTHONPATH=src python -m pytest tests/test_status.py::TestAgentStatus -v`
Expected: FAIL with `AttributeError: 'KanbanAgent' object has no attribute 'current_status'`

- [ ] **Step 4: Add status holder + helper to agent**

In `src/kanban_agent/agent.py`, add import near top with the other relative imports:

```python
from .status import AgentStatus, StatusHolder
```

In `KanbanAgent.__init__`, after `self._shutdown_event = asyncio.Event()`, add:

```python
        self._status = StatusHolder()
```

Add two methods on `KanbanAgent` (place them just above `def shutdown`):

```python
    @property
    def current_status(self) -> AgentStatus:
        return self._status.get()

    def _set_status(self, **changes) -> None:
        self._status.update(**changes)
```

- [ ] **Step 5: Write status at lifecycle points**

Edit `KanbanAgent.run`:

After `await self.board.initialize()` and the `_agent_username` line, before the `while not self._shutdown_event.is_set():` loop, add:

```python
        self._set_status(state="running", current_phase="polling")
```

At the very top of `run` (before `await self.board.initialize()`), add:

```python
        self._set_status(state="starting")
```

In `shutdown`, after `self._shutdown_event.set()`, add:

```python
        self._set_status(state="stopped", current_issue=None, current_phase=None)
```

In `_handle_new_task` (find it via `grep -n _handle_new_task src/kanban_agent/agent.py`), at the start of the method body, add:

```python
        self._set_status(state="running", current_issue=task.issue_number, current_phase="executing")
```

At the end of `_handle_new_task` (just before the method returns — find the last line of the function body), add:

```python
        self._set_status(state="running", current_issue=None, current_phase="polling")
```

If `_handle_new_task` has multiple early returns, add the cleanup line right before each return. Verify by re-reading the method after editing.

- [ ] **Step 6: Run tests, expect pass**

Run: `cd /Users/I572881/workspace/Head-Nurse && PYTHONPATH=src python -m pytest tests/ -v`
Expected: all green (13 passed total)

- [ ] **Step 7: Commit**

```bash
git add src/kanban_agent/agent.py tests/test_status.py
git commit -m "feat(agent): expose current_status and write at lifecycle points"
```

---

## Task 3: Icon rendering script

**Files:**
- Create: `scripts/render_icons.py`
- Create: `resources/icon-on.png`, `resources/icon-off.png` (generated, committed)

- [ ] **Step 1: Create render script**

Create `scripts/render_icons.py`:

```python
"""Render menubar icons once at build time. Run: python scripts/render_icons.py"""
from pathlib import Path

from AppKit import NSBitmapImageRep, NSImage, NSPNGFileType
from Foundation import NSMakeRect

OUT = Path(__file__).resolve().parent.parent / "resources"
OUT.mkdir(exist_ok=True)

SYMBOLS = {
    "icon-on.png": "circle.fill",
    "icon-off.png": "circle",
}
SIZE = 18  # menubar template icon point size

for filename, symbol in SYMBOLS.items():
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, None)
    if image is None:
        raise SystemExit(f"SF Symbol {symbol!r} not available; macOS 11+ required")
    image.setSize_((SIZE, SIZE))
    image.setTemplate_(True)

    # Render to bitmap → PNG bytes
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, SIZE * 2, SIZE * 2, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0
    )
    rep.setSize_((SIZE, SIZE))

    from AppKit import NSGraphicsContext
    NSGraphicsContext.saveGraphicsState()
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.setCurrentContext_(ctx)
    image.drawInRect_(NSMakeRect(0, 0, SIZE, SIZE))
    NSGraphicsContext.restoreGraphicsState()

    data = rep.representationUsingType_properties_(NSPNGFileType, {})
    out_path = OUT / filename
    data.writeToFile_atomically_(str(out_path), True)
    print(f"wrote {out_path}")
```

- [ ] **Step 2: Run script**

Run: `cd /Users/I572881/workspace/Head-Nurse && /opt/homebrew/bin/python3 scripts/render_icons.py`
Expected output:
```
wrote .../resources/icon-on.png
wrote .../resources/icon-off.png
```

If `ImportError: No module named AppKit`, run: `/opt/homebrew/bin/python3 -m pip install pyobjc-framework-Cocoa` and retry.

- [ ] **Step 3: Verify files exist and are non-empty**

Run: `ls -l resources/icon-*.png`
Expected: two files, each > 100 bytes.

- [ ] **Step 4: Commit**

```bash
git add scripts/render_icons.py resources/icon-on.png resources/icon-off.png
git commit -m "feat(icons): render circle/circle.fill SF symbols to PNG"
```

---

## Task 4: Menubar app skeleton

**Files:**
- Create: `src/kanban_agent/menubar.py`
- Modify: `pyproject.toml` — add `rumps` dependency, add `kanban-menubar` script

- [ ] **Step 1: Add rumps dependency**

Edit `pyproject.toml`. Replace the `dependencies` line and add a script entry:

```toml
[project]
name = "kanban-agent"
version = "0.1.0"
description = "GitHub Project V2 kanban agent for remote task execution"
requires-python = ">=3.11"
dependencies = ["pyyaml>=6.0", "rumps>=0.4.0"]

[project.optional-dependencies]
dev = ["pytest>=7.0", "py2app>=0.28"]

[project.scripts]
kanban-agent = "kanban_agent.__main__:main"
kanban-menubar = "kanban_agent.menubar:main"
```

- [ ] **Step 2: Install dependency**

Run: `cd /Users/I572881/workspace/Head-Nurse && /opt/homebrew/bin/python3 -m pip install -e ".[dev]"`
Expected: installs rumps, py2app, pytest without error.

- [ ] **Step 3: Create menubar.py**

Create `src/kanban_agent/menubar.py`:

```python
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

import rumps

from .agent import KanbanAgent
from .config import Config
from .logging_setup import setup_logging
from .status import AgentStatus, StatusHolder

logger = logging.getLogger(__name__)

RESOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "resources"
POLL_INTERVAL = 0.5


def _icon_path(name: str) -> str:
    # When packaged by py2app, resources live next to the executable.
    bundle = Path(sys.executable).parent.parent / "Resources" / f"{name}.png"
    if bundle.exists():
        return str(bundle)
    return str(RESOURCES_DIR / f"{name}.png")


class MenubarApp:
    def __init__(self) -> None:
        self.app = rumps.App("HeadNurse", icon=_icon_path("icon-off"), template=True, quit_button=None)
        self.app.menu = [
            "status_row",
            None,
            "toggle",
            "restart",
            None,
            "open_config",
            "view_logs",
            None,
            "quit",
        ]
        self._configure_menu()

        self._agent: Optional[KanbanAgent] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._fallback_status = StatusHolder()  # used before agent exists / when stopped
        self._notified_crash = False
        self._notified_no_config = False

        self._timer = rumps.Timer(self._tick, POLL_INTERVAL)
        self._timer.start()

    def _configure_menu(self) -> None:
        m = self.app.menu
        m["status_row"].title = "○ Stopped"
        m["status_row"].set_callback(None)  # disabled
        m["toggle"].title = "Start Agent"
        m["toggle"].set_callback(self._on_toggle)
        m["restart"].title = "Restart Agent"
        m["restart"].set_callback(self._on_restart)
        m["restart"].set_callback(None)  # initially disabled
        m["open_config"].title = "Open Config…"
        m["open_config"].set_callback(self._on_open_config)
        m["view_logs"].title = "View Logs…"
        m["view_logs"].set_callback(self._on_view_logs)
        m["quit"].title = "Quit"
        m["quit"].set_callback(self._on_quit)

    # ── Lifecycle ────────────────────────────────────────────────────────
    def _start_agent(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        try:
            config = Config.load()
        except FileNotFoundError:
            self._fallback_status.set(AgentStatus(state="stopped", error="no_config"))
            if not self._notified_no_config:
                rumps.notification("Kanban Agent", "Config not found", "Click Open Config… to create one.")
                self._notified_no_config = True
            return
        self._notified_no_config = False
        self._notified_crash = False

        setup_logging(config.log_file, config.log_level)
        self._fallback_status.set(AgentStatus(state="starting"))
        self._agent = KanbanAgent(config)
        self._thread = threading.Thread(target=self._run_agent, name="kanban-agent", daemon=True)
        self._thread.start()

    def _run_agent(self) -> None:
        assert self._agent is not None
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._agent.run())
        except Exception:
            logger.exception("Agent crashed")
            self._agent._set_status(state="crashed", error="see logs")
        finally:
            loop.close()
            self._loop = None

    def _stop_agent(self, timeout: float = 5.0) -> None:
        if not self._agent:
            return
        agent = self._agent
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(agent.shutdown)
        else:
            agent.shutdown()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._agent = None
        self._thread = None
        self._fallback_status.set(AgentStatus(state="stopped"))

    # ── Status polling ───────────────────────────────────────────────────
    def _current_status(self) -> AgentStatus:
        if self._agent is not None and self._thread and self._thread.is_alive():
            return self._agent.current_status
        if self._agent is not None and self._thread and not self._thread.is_alive():
            # Thread died unexpectedly
            return AgentStatus(state="crashed", error="see logs")
        return self._fallback_status.get()

    def _tick(self, _sender) -> None:
        status = self._current_status()
        m = self.app.menu
        m["status_row"].title = status.menu_text
        self.app.icon = _icon_path(status.icon_name)

        running_like = status.state in ("running", "starting")
        m["toggle"].title = "Stop Agent" if running_like else "Start Agent"
        m["restart"].set_callback(self._on_restart if running_like else None)

        if status.state == "crashed" and not self._notified_crash:
            rumps.notification("Kanban Agent", "Agent crashed", "Click View Logs… for details.")
            self._notified_crash = True

    # ── Menu callbacks ───────────────────────────────────────────────────
    def _on_toggle(self, _sender) -> None:
        if self._agent is None:
            self._start_agent()
        else:
            self._stop_agent()

    def _on_restart(self, _sender) -> None:
        self._stop_agent()
        self._start_agent()

    def _on_open_config(self, _sender) -> None:
        path = Path.home() / ".config" / "kanban-agent" / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("# kanban-agent config — see README\n")
        subprocess.run(["open", str(path)], check=False)

    def _on_view_logs(self, _sender) -> None:
        log_path = Path.home() / "Library" / "Logs" / "kanban-agent-stdout.log"
        if not log_path.exists():
            log_path = log_path.parent
        subprocess.run(["open", str(log_path)], check=False)

    def _on_quit(self, _sender) -> None:
        self._stop_agent(timeout=3.0)
        rumps.quit_application()

    def run(self) -> None:
        self._start_agent()  # auto-start
        self.app.run()


def main() -> None:
    MenubarApp().run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Smoke-launch the app from terminal**

Run: `cd /Users/I572881/workspace/Head-Nurse && /opt/homebrew/bin/python3 -m kanban_agent.menubar`
Expected: the menubar shows a small circle icon. Click → menu appears with Stop Agent / Restart / Open Config / View Logs / Quit. Click Quit → process exits.

If config is missing, the icon stays empty-circle and clicking Stop/Start cycles between "○ No config" and notification. That's the intended config-missing path.

Press Ctrl-C in terminal if Quit menu doesn't respond (rare during dev).

- [ ] **Step 5: Commit**

```bash
git add src/kanban_agent/menubar.py pyproject.toml
git commit -m "feat(menubar): rumps app hosting agent in worker thread"
```

---

## Task 5: py2app bundling

**Files:**
- Create: `setup_app.py`

- [ ] **Step 1: Create py2app config**

Create `setup_app.py`:

```python
"""Build HeadNurse.app: python setup_app.py py2app"""
from setuptools import setup

APP = ["src/kanban_agent/menubar.py"]
DATA_FILES = [("", ["resources/icon-on.png", "resources/icon-off.png"])]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "HeadNurse",
        "CFBundleDisplayName": "HeadNurse",
        "CFBundleIdentifier": "com.kanban-agent.menubar",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    },
    "packages": ["kanban_agent", "rumps", "yaml"],
    "includes": ["kanban_agent.agent", "kanban_agent.menubar", "kanban_agent.status"],
}

setup(
    app=APP,
    name="HeadNurse",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
```

- [ ] **Step 2: Build the app**

Run: `cd /Users/I572881/workspace/Head-Nurse && rm -rf build dist && /opt/homebrew/bin/python3 setup_app.py py2app`
Expected: ends with `*** done ***` and `dist/HeadNurse.app/` exists.

If build fails with missing modules, add them to `OPTIONS["packages"]` or `includes` and retry.

- [ ] **Step 3: Launch the bundled app**

Run: `open dist/HeadNurse.app`
Expected: menubar icon appears within 2 seconds. Menu works as in Task 4 step 4. No Dock icon (LSUIElement).

If the app silently exits, run from the bundle binary directly to see traceback:
`./dist/HeadNurse.app/Contents/MacOS/HeadNurse`

- [ ] **Step 4: Quit and clean build artifacts**

Click Quit in the menu, then:

```bash
rm -rf build  # keep dist for now; setup.sh will rebuild
```

- [ ] **Step 5: Add build dirs to .gitignore**

Edit `.gitignore`, append:

```
build/
dist/
*.egg-info/
__pycache__/
```

(Leave any existing lines alone — append the missing ones.)

- [ ] **Step 6: Commit**

```bash
git add setup_app.py .gitignore
git commit -m "feat(build): py2app config for HeadNurse.app"
```

---

## Task 6: Update setup.sh and remove launchd

**Files:**
- Modify: `setup.sh`
- Delete: `launchd/com.kanban-agent.plist`, `launchd/`

- [ ] **Step 1: Read current setup.sh end-to-end**

Run: `cat setup.sh`

Identify the launchd-install section (search for `launchctl` or `LaunchAgents` or `plist`). Note the line range so you can replace it.

- [ ] **Step 2: Rewrite setup.sh**

Replace the entire file with:

```bash
#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="${HOME}/.config/kanban-agent"
APP_DEST="${HOME}/Applications/HeadNurse.app"
OLD_PLIST="${HOME}/Library/LaunchAgents/com.kanban-agent.plist"

echo "Step 1: Verifying Python 3.11+ at /opt/homebrew/bin/python3"
/opt/homebrew/bin/python3 -c 'import sys; assert sys.version_info >= (3,11), sys.version'

echo "Step 2: Installing kanban-agent and dev deps"
/opt/homebrew/bin/python3 -m pip install -e ".[dev]"

echo "Step 3: Rendering icons"
/opt/homebrew/bin/python3 scripts/render_icons.py

echo "Step 4: Building HeadNurse.app"
rm -rf build dist
/opt/homebrew/bin/python3 setup_app.py py2app

echo "Step 5: Installing app to ~/Applications"
mkdir -p "${HOME}/Applications"
rm -rf "${APP_DEST}"
cp -R dist/HeadNurse.app "${APP_DEST}"

echo "Step 6: Migrating from launchd if present"
if [ -f "${OLD_PLIST}" ]; then
    launchctl unload "${OLD_PLIST}" 2>/dev/null || true
    rm -f "${OLD_PLIST}"
    echo "  ✓ Removed old launchd plist"
fi

echo "Step 7: Ensuring config exists"
if [ ! -f "${CONFIG_DIR}/config.yaml" ]; then
    mkdir -p "${CONFIG_DIR}"
    cp config.example.yaml "${CONFIG_DIR}/config.yaml"
    echo "  ✓ Created ${CONFIG_DIR}/config.yaml — edit it before first run."
fi

echo ""
echo "✓ Done. Launch with: open ${APP_DEST}"
echo "  To auto-start at login: System Settings → General → Login Items → +,"
echo "  and add ${APP_DEST}."
```

- [ ] **Step 3: Make sure setup.sh stays executable**

Run: `chmod +x setup.sh && ls -l setup.sh`
Expected: `-rwxr-xr-x ...`

- [ ] **Step 4: Delete launchd directory**

Run: `git rm -r launchd/`
Expected: `launchd/com.kanban-agent.plist` removed.

- [ ] **Step 5: Run setup.sh end-to-end**

Run: `./setup.sh`
Expected: completes without error, prints "✓ Done" and the Login Items hint. `~/Applications/HeadNurse.app` exists.

- [ ] **Step 6: Launch installed app**

Run: `open ~/Applications/HeadNurse.app`
Expected: menubar icon appears, menu works. Click Quit to close.

- [ ] **Step 7: Commit**

```bash
git add setup.sh
git commit -m "feat(setup): replace launchd install with py2app build"
```

---

## Task 7: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read current README**

Run: `cat README.md`

- [ ] **Step 2: Rewrite Setup and Run sections**

Edit `README.md`. Replace the `## Setup` and `## Run manually` sections with:

```markdown
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

## Run manually (headless)

For debugging or non-GUI use:

```bash
python -m kanban_agent
```

This skips the menubar and runs the agent directly in the foreground.
```

(Leave Configuration / Architecture sections unchanged.)

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): document HeadNurse.app install and usage"
```

---

## Task 8: Final manual verification

**Files:** none — manual test pass.

- [ ] **Step 1: Quit any running instance**

Run: `pkill -f HeadNurse || true; pkill -f kanban_agent || true`

- [ ] **Step 2: Launch app**

Run: `open ~/Applications/HeadNurse.app`
Expected: filled circle icon (template style) appears in menubar within 2 seconds.

- [ ] **Step 3: Verify status shows Running**

Click icon. Status row reads `● Running · idle` or `● Running · #N <phase>`. Toggle item is "Stop Agent". Restart enabled.

- [ ] **Step 4: Stop**

Click "Stop Agent". Within 1 second: icon flips to empty circle, status reads `○ Stopped`. Toggle is "Start Agent". Restart disabled.

- [ ] **Step 5: Start**

Click "Start Agent". Status goes `● Starting…` → `● Running · idle`.

- [ ] **Step 6: Restart**

Click "Restart Agent". Verify quick stopped→running cycle (status briefly shows Stopped or Starting then Running).

- [ ] **Step 7: Open Config / View Logs**

Click "Open Config…" — `~/.config/kanban-agent/config.yaml` opens in default editor.
Click "View Logs…" — log file or Logs folder opens.

- [ ] **Step 8: Crash detection**

Stop the agent. Edit `~/.config/kanban-agent/config.yaml` to invalid YAML (e.g., `: : : :`). Click "Start Agent". Within 2 seconds: status reads `○ Crashed · see logs`, icon stays empty circle, system notification fires once. Restore the config.

- [ ] **Step 9: No-config scenario**

Stop. Move config aside: `mv ~/.config/kanban-agent/config.yaml /tmp/config.yaml.bak`. Click Start. Status reads `○ No config`, notification fires. Restore: `mv /tmp/config.yaml.bak ~/.config/kanban-agent/config.yaml`. Click Start, verify recovery.

- [ ] **Step 10: Quit cleanup**

Click Quit. Run: `pgrep -f HeadNurse; pgrep -f kanban_agent`
Expected: both empty (no orphan processes).

- [ ] **Step 11: Commit verification log if anything changed**

If you fixed bugs along the way, ensure those fixes are committed. Otherwise nothing to commit here.

---

## Self-Review Notes

- All 6 menu_text variants from spec are tested in Task 1.
- Status writes at lifecycle points (Task 2) cover all transitions in spec § Data Flow.
- Two-state icon (Task 3, 4) matches spec § Icons.
- Crash + no_config notification (Task 4 + Task 8 step 8/9) covers spec § Crash detection / Config missing.
- `LSUIElement` set in py2app plist (Task 5) → no Dock icon as required.
- Migration from launchd handled in Task 6 step 2 (`launchctl unload` + `rm`).
- Auto-start on login deferred to user (System Settings) per spec — README documents it (Task 7).
- Spec deletes `launchd/` directory entirely → Task 6 step 4 does `git rm -r launchd/`.
