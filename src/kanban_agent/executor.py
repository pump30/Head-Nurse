import asyncio
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Config
from .models import Task

logger = logging.getLogger(__name__)

MAX_COMMENT_LENGTH = 60000

# Build a subprocess env with well-known PATH entries for macOS GUI apps.
_EXTRA_PATHS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / ".nvm" / "versions" / "node" / "v22.22.0" / "bin"),
]


def _subprocess_env() -> dict[str, str]:
    """Return an env dict with PATH augmented for macOS .app bundles."""
    env = os.environ.copy()
    existing = env.get("PATH", "/usr/bin:/bin")
    prepend = ":".join(p for p in _EXTRA_PATHS if Path(p).is_dir())
    env["PATH"] = f"{prepend}:{existing}" if prepend else existing
    return env


def _resolve_claude(config_command: str) -> str:
    """Resolve the claude command to an absolute path if possible."""
    if os.path.isabs(config_command):
        return config_command
    # Check well-known locations
    for base in _EXTRA_PATHS:
        candidate = os.path.join(base, config_command)
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which(config_command)
    return found or config_command


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    session_id: Optional[str] = None
    timed_out: bool = False
    needs_input: bool = False


class TaskExecutor:
    def __init__(self, config: Config):
        self.config = config
        self._running: dict[int, asyncio.subprocess.Process] = {}
        self._claude_bin = _resolve_claude(config.claude_command)
        self._env = _subprocess_env()

    async def execute_task(self, task: Task, prompt: str) -> ExecutionResult:
        if not prompt or not prompt.strip():
            return ExecutionResult(
                stdout="",
                stderr="Empty prompt: issue has no body or title",
                exit_code=1,
                session_id="",
            )

        session_id = task.claude_session_id or str(uuid.uuid4())

        cmd = [
            self._claude_bin,
            "-p",
            prompt,
            "--output-format", "text",
            "--permission-mode", self.config.claude_permission_mode,
            "--max-budget-usd", str(self.config.max_budget_per_task_usd),
        ]
        if task.claude_session_id:
            cmd.extend(["--resume", session_id])
        else:
            cmd.extend(["--session-id", session_id])

        logger.info("Executing task #%d (session=%s)", task.issue_number, session_id)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.config.claude_working_dir,
            env=self._env,
        )
        self._running[task.issue_number] = proc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.config.task_timeout_seconds,
            )
            output = stdout.decode(errors="replace")
            return ExecutionResult(
                stdout=output,
                stderr=stderr.decode(errors="replace"),
                exit_code=proc.returncode or 0,
                session_id=session_id,
                needs_input=self._detect_needs_input(output),
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecutionResult(
                stdout="",
                stderr=f"Task timed out after {self.config.task_timeout_seconds}s",
                exit_code=-1,
                session_id=session_id,
                timed_out=True,
            )
        finally:
            self._running.pop(task.issue_number, None)

    async def cancel_task(self, issue_number: int) -> bool:
        proc = self._running.get(issue_number)
        if proc:
            proc.kill()
            await proc.wait()
            self._running.pop(issue_number, None)
            return True
        return False

    @staticmethod
    def _detect_needs_input(output: str) -> bool:
        text = output.strip()
        if not text:
            return False
        last_lines = "\n".join(text.split("\n")[-5:])
        if re.search(r"\?\s*$", last_lines):
            return True
        if re.search(r"^\s*\d+\.\s+.+\n\s*\d+\.\s+", last_lines, re.MULTILINE):
            return True
        if re.search(r"^\s*[-•]\s+.+\n\s*[-•]\s+", last_lines, re.MULTILINE):
            return True
        input_phrases = [
            "which option", "please choose", "please select",
            "would you like", "do you want", "what would you prefer",
            "let me know", "your choice", "pick one",
            "你想", "你选", "请选择", "哪个方案", "你觉得",
        ]
        lower = last_lines.lower()
        if any(phrase in lower for phrase in input_phrases):
            return True
        return False

    ICON = '<img src="https://cdn.simpleicons.org/anthropic" width="14">'

    @staticmethod
    def format_result_comment(result: ExecutionResult) -> str:
        icon = TaskExecutor.ICON

        if result.timed_out:
            status = "timed out"
            content = f"```\n{result.stderr}\n```"
        elif result.needs_input:
            status = "waiting for input"
            content = result.stdout.strip()
            if len(content) > MAX_COMMENT_LENGTH:
                content = content[:MAX_COMMENT_LENGTH] + "\n\n... (truncated)"
        elif result.exit_code == 0:
            status = "completed"
            content = result.stdout.strip()
            if len(content) > MAX_COMMENT_LENGTH:
                content = content[:MAX_COMMENT_LENGTH] + "\n\n... (truncated)"
        else:
            status = f"failed (exit {result.exit_code})"
            output = result.stdout.strip()[:5000]
            error_info = result.stderr.strip()[:3000] if result.stderr else "No error details"
            content = f"{output}\n\n```\n{error_info}\n```"

        header = f"{icon} **Claude Code** · {status}"
        return (
            f"<table><tr><td>\n\n"
            f"{header}\n\n"
            f"---\n\n"
            f"{content}\n\n"
            f"</td></tr></table>"
        )
