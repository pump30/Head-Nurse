from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import os
import yaml


@dataclass
class CalendarSyncConfig:
    caldav_url: str
    caldav_username: str
    caldav_password: str
    enabled: bool = True
    interval_seconds: int = 900
    days_ahead: int = 14
    outlook_token_file: str = "~/.sap-mcp/cookies/outlook/sap_tokens.json"
    state_file: str = "~/.local/state/head-nurse/calendar-sync-state.json"

    def __post_init__(self):
        self.outlook_token_file = str(Path(self.outlook_token_file).expanduser())
        self.state_file = str(Path(self.state_file).expanduser())
        # Support ${ENV_VAR} in caldav_password
        if self.caldav_password.startswith("${") and self.caldav_password.endswith("}"):
            env_var = self.caldav_password[2:-1]
            self.caldav_password = os.environ.get(env_var, "")


@dataclass
class Config:
    repo: str
    project_number: int
    poll_interval_seconds: int = 30
    claude_command: str = "claude"
    claude_working_dir: str = "~/Projects"
    claude_permission_mode: str = "acceptEdits"
    task_timeout_seconds: int = 600
    max_concurrent_tasks: int = 1
    max_budget_per_task_usd: float = 0  # 0 = unlimited
    log_file: str = "~/Library/Logs/kanban-agent.log"
    log_level: str = "INFO"
    state_file: str = "~/.local/state/kanban-agent/state.json"
    calendar_sync: Optional[CalendarSyncConfig] = None

    def __post_init__(self):
        self.claude_working_dir = str(Path(self.claude_working_dir).expanduser())
        self.log_file = str(Path(self.log_file).expanduser())
        self.state_file = str(Path(self.state_file).expanduser())

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        if path is None:
            path = os.environ.get(
                "KANBAN_AGENT_CONFIG",
                str(Path.home() / ".config" / "kanban-agent" / "config.yaml"),
            )
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        calendar_sync_data = data.pop("calendar_sync", None)
        calendar_sync = None
        if calendar_sync_data and isinstance(calendar_sync_data, dict):
            calendar_sync = CalendarSyncConfig(
                **{k: v for k, v in calendar_sync_data.items()
                   if k in CalendarSyncConfig.__dataclass_fields__}
            )

        known_fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known_fields, calendar_sync=calendar_sync)
