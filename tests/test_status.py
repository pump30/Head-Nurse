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


class TestAgentStatus:
    def _make_config(self, tmp_path):
        from kanban_agent.config import Config
        return Config(
            repo="x/y",
            project_number=1,
            poll_interval_seconds=30,
            claude_command="echo",
            claude_working_dir=str(tmp_path),
            claude_permission_mode="acceptEdits",
            task_timeout_seconds=10,
            max_budget_per_task_usd=1.0,
            log_file=str(tmp_path / "log"),
            log_level="INFO",
            state_file=str(tmp_path / "state.json"),
        )

    def test_agent_starts_stopped(self, tmp_path):
        from kanban_agent.agent import KanbanAgent
        agent = KanbanAgent(self._make_config(tmp_path))
        assert agent.current_status.state == "stopped"

    def test_set_status_updates(self, tmp_path):
        from kanban_agent.agent import KanbanAgent
        agent = KanbanAgent(self._make_config(tmp_path))
        agent._set_status(state="running", current_phase="polling")
        s = agent.current_status
        assert s.state == "running"
        assert s.current_phase == "polling"

    def test_shutdown_writes_stopped(self, tmp_path):
        from kanban_agent.agent import KanbanAgent
        agent = KanbanAgent(self._make_config(tmp_path))
        agent._set_status(state="running", current_issue=42, current_phase="executing")
        agent.shutdown()
        s = agent.current_status
        assert s.state == "stopped"
        assert s.current_issue is None
        assert s.current_phase is None
