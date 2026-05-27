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
