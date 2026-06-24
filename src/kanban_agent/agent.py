import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from .config import Config
from .github import GitHubClient
from .models import Task, TaskStatus
from .project_board import ProjectBoard
from .executor import TaskExecutor, ExecutionResult
from .status import AgentStatus, StatusHolder

logger = logging.getLogger(__name__)


class AgentState:
    def __init__(self, state_file: str):
        self._path = Path(state_file)
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            with open(self._path) as f:
                self._data = json.load(f)
        else:
            self._data = {"in_progress": {}, "completed": {}}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    def mark_in_progress(self, task: Task) -> None:
        self._data["in_progress"][str(task.issue_number)] = {
            "project_item_id": task.project_item_id,
            "claude_session_id": task.claude_session_id,
            "last_processed_comment_id": task.last_processed_comment_id,
        }
        self._save()

    def mark_completed(self, task: Task, session_id: Optional[str] = None) -> None:
        in_progress_entry = self._data["in_progress"].pop(str(task.issue_number), {})
        existing_completed = self._data["completed"].get(str(task.issue_number), {})
        self._data["completed"][str(task.issue_number)] = {
            "claude_session_id": session_id,
            "project_item_id": in_progress_entry.get("project_item_id") or task.project_item_id,
            "last_processed_comment_id": (
                in_progress_entry.get("last_processed_comment_id")
                or existing_completed.get("last_processed_comment_id")
            ),
        }
        self._save()

    def mark_failed(self, task: Task) -> None:
        self._data["in_progress"].pop(str(task.issue_number), None)
        self._save()

    def get_in_progress_issues(self) -> dict[int, dict]:
        return {int(k): v for k, v in self._data.get("in_progress", {}).items()}

    def get_session_id(self, issue_number: int) -> Optional[str]:
        entry = self._data.get("in_progress", {}).get(str(issue_number))
        if entry:
            return entry.get("claude_session_id")
        entry = self._data.get("completed", {}).get(str(issue_number))
        if entry:
            return entry.get("claude_session_id")
        return None

    def get_last_comment_id(self, issue_number: int) -> Optional[str]:
        for store in ("in_progress", "completed"):
            entry = self._data.get(store, {}).get(str(issue_number))
            if entry and entry.get("last_processed_comment_id"):
                return entry["last_processed_comment_id"]
        return None

    def update_last_comment_id(self, issue_number: int, comment_id: str) -> None:
        for store in ("in_progress", "completed"):
            entry = self._data.get(store, {}).get(str(issue_number))
            if entry is not None:
                entry["last_processed_comment_id"] = comment_id
                self._save()
                return

    def get_all_tracked_issues(self) -> dict[int, dict]:
        result = {}
        for store in ("in_progress", "completed"):
            for k, v in self._data.get(store, {}).items():
                result[int(k)] = v
        return result


class KanbanAgent:
    def __init__(self, config: Config):
        self.config = config
        self.github = GitHubClient(config.repo)
        self.board = ProjectBoard(self.github, config.project_number)
        self.executor = TaskExecutor(config)
        self.state = AgentState(config.state_file)
        self._shutdown_event = asyncio.Event()
        self._status = StatusHolder()
        self._agent_username: Optional[str] = None

    async def run(self) -> None:
        self._set_status(state="starting")
        await self.board.initialize()
        self._agent_username = await self.github.get_authenticated_user()
        logger.info(
            "Kanban Agent started (user=%s, repo=%s, poll=%ds)",
            self._agent_username, self.config.repo, self.config.poll_interval_seconds,
        )
        self._set_status(state="running", current_phase="polling")

        while not self._shutdown_event.is_set():
            try:
                await self._poll_cycle()
            except Exception:
                logger.exception("Error in poll cycle")

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self.config.poll_interval_seconds,
                )
                break
            except asyncio.TimeoutError:
                pass

    async def _poll_cycle(self) -> None:
        # 1. Pick up new tasks from Inbox
        inbox_tasks = await self.board.get_inbox_tasks()
        for task in inbox_tasks:
            if self._shutdown_event.is_set():
                break
            await self._handle_new_task(task)

        # 2. Check for follow-up comments on in-progress/completed tasks
        await self._check_follow_ups()

    async def _handle_new_task(self, task: Task) -> None:
        logger.info("Picking up task #%d: %s", task.issue_number, task.title)
        self._set_status(state="running", current_issue=task.issue_number, current_phase="executing")

        await self.board.move_to_status(task.project_item_id, TaskStatus.IN_PROGRESS)
        self.state.mark_in_progress(task)

        prompt = task.body or task.title
        result = await self._execute_and_post(task, prompt)
        await self._finalize_task(task, result)
        self._set_status(current_issue=None, current_phase="polling")

    async def _check_follow_ups(self) -> None:
        tracked = self.state.get_all_tracked_issues()

        for issue_number, info in tracked.items():
            comments = await self.github.get_issue_comments(issue_number)
            if not comments:
                continue

            last_id = self.state.get_last_comment_id(issue_number)
            new_comments = self._filter_new_user_comments(comments, last_id)

            if new_comments:
                latest = new_comments[-1]
                await self._handle_follow_up(issue_number, info, latest)

    _AGENT_PREFIXES = ("<table>", "---\n<img", "🤖", "✅", "❌", "⏱️", "> **🤖")

    def _filter_new_user_comments(self, comments: list[dict], last_id: Optional[str]) -> list[dict]:
        found_last = last_id is None
        result = []
        for c in comments:
            if not found_last:
                if c.get("id") == last_id or c.get("url", "").endswith(f"/{last_id}"):
                    found_last = True
                continue
            body = c.get("body", "").strip()
            if any(body.startswith(p) for p in self._AGENT_PREFIXES):
                continue
            result.append(c)
        return result

    async def _handle_follow_up(self, issue_number: int, info: dict, comment: dict) -> None:
        logger.info("Follow-up on #%d: %s", issue_number, comment["body"][:80])
        self._set_status(state="running", current_issue=issue_number, current_phase="executing")

        task = Task(
            issue_number=issue_number,
            issue_node_id="",
            project_item_id=info.get("project_item_id", ""),
            title="",
            body=comment["body"],
            status=TaskStatus.IN_PROGRESS,
            claude_session_id=info.get("claude_session_id"),
        )

        if task.project_item_id:
            await self.board.move_to_status(task.project_item_id, TaskStatus.IN_PROGRESS)

        result = await self._execute_and_post(task, comment["body"])

        comment_id = comment.get("id") or comment.get("url", "").split("/")[-1]
        self.state.update_last_comment_id(issue_number, comment_id)

        await self._finalize_task(task, result)
        self._set_status(current_issue=None, current_phase="polling")

    async def _execute_and_post(self, task: Task, prompt: str) -> ExecutionResult:
        placeholder = f"---\n{TaskExecutor.ICON} **Claude Code** · executing..."
        comment_id = await self.github.add_comment_and_get_id(task.issue_number, placeholder)

        result = await self.executor.execute_task(task, prompt)

        body = TaskExecutor.format_result_comment(result)
        if comment_id:
            await self.github.edit_comment(comment_id, body)
        else:
            await self.github.add_comment(task.issue_number, body)

        return result

    async def _finalize_task(self, task: Task, result: ExecutionResult) -> None:
        if result.needs_input:
            if task.project_item_id:
                await self.board.move_to_status(task.project_item_id, TaskStatus.WAITING)
            task.claude_session_id = result.session_id
            self.state.mark_in_progress(task)
            logger.info("Task #%d waiting for user input", task.issue_number)
        elif result.exit_code == 0:
            if task.project_item_id:
                await self.board.move_to_status(task.project_item_id, TaskStatus.COMPLETED)
            self.state.mark_completed(task, result.session_id)
            logger.info("Task #%d completed successfully", task.issue_number)
        else:
            if task.project_item_id:
                await self.board.move_to_status(task.project_item_id, TaskStatus.FAILED)
            self.state.mark_failed(task)
            logger.warning("Task #%d failed (exit=%d)", task.issue_number, result.exit_code)

    @property
    def current_status(self) -> AgentStatus:
        return self._status.get()

    def _set_status(self, **changes) -> None:
        self._status.update(**changes)

    def shutdown(self) -> None:
        logger.info("Shutdown requested")
        self._shutdown_event.set()
        self._set_status(state="stopped", current_issue=None, current_phase=None)
