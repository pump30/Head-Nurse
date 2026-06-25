from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import os
import yaml


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

        with open(config_path) as f:
            data = yaml.safe_load(f)

        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
