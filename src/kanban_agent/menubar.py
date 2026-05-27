from __future__ import annotations

import asyncio
import logging
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
        self.app = rumps.App(
            "HeadNurse",
            icon=_icon_path("icon-off"),
            template=True,
            quit_button=None,
        )
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
        self._fallback_status = StatusHolder()
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
                rumps.notification(
                    "Kanban Agent",
                    "Config not found",
                    "Click Open Config… to create one.",
                )
                self._notified_no_config = True
            return
        self._notified_no_config = False
        self._notified_crash = False

        setup_logging(config.log_file, config.log_level)
        self._fallback_status.set(AgentStatus(state="starting"))
        self._agent = KanbanAgent(config)
        self._thread = threading.Thread(
            target=self._run_agent, name="kanban-agent", daemon=True
        )
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
            if self._agent is not None:
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
            rumps.notification(
                "Kanban Agent",
                "Agent crashed",
                "Click View Logs… for details.",
            )
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
