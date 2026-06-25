# Outlook → CalDAV Calendar Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a calendar sync module to Head-Nurse that mirrors Outlook events to a CalDAV (Radicale) server every 15 minutes.

**Architecture:** A new `CalendarSync` class runs as an asyncio task alongside the existing `KanbanAgent`. It reads Outlook's OAuth token from `~/.sap-mcp/cookies/outlook/sap_tokens.json`, fetches calendar events via the Outlook REST API, diffs against a local state file, and writes/updates/deletes events on a CalDAV server using iCalendar format.

**Tech Stack:** Python 3.11+, httpx (async HTTP), icalendar (VEVENT generation), caldav (CalDAV client), existing rumps menubar infrastructure.

## Global Constraints

- Python ≥ 3.11
- All new code in `src/kanban_agent/`
- Tests in `tests/`
- Async-first (asyncio) — sync libraries wrapped with `run_in_executor`
- Token file path: `~/.sap-mcp/cookies/outlook/sap_tokens.json`
- State file path: `~/.local/state/head-nurse/calendar-sync-state.json`
- OAuth tenant ID: `69b863e3-480a-4ee9-8bd0-20a8adb6909b`
- OAuth client ID: `9199bf20-a13f-4107-85dc-02114787ef48`
- Token endpoint: `https://login.microsoftonline.com/69b863e3-480a-4ee9-8bd0-20a8adb6909b/oauth2/v2.0/token`
- Outlook API base: `https://outlook.office.com/api/v2.0`
- OAuth scopes: `offline_access https://outlook.office.com/Mail.ReadWrite https://outlook.office.com/Mail.Send https://outlook.office.com/Calendars.ReadWrite https://outlook.office.com/People.Read https://outlook.office.com/User.Read`

## File Structure

```
src/kanban_agent/
├── calendar_sync.py      ← NEW: CalendarSync class, async sync loop, diff logic
├── outlook_token.py      ← NEW: Token reader + refresh from sap_tokens.json
├── config.py             ← MODIFY: Add CalendarSyncConfig dataclass
├── menubar.py            ← MODIFY: Start CalendarSync, add menu item
├── ...                   (existing files unchanged)

tests/
├── test_outlook_token.py ← NEW
├── test_calendar_sync.py ← NEW
```

---

### Task 1: Config extension + Outlook token reader

**Files:**
- Modify: `src/kanban_agent/config.py`
- Create: `src/kanban_agent/outlook_token.py`
- Create: `tests/test_outlook_token.py`
- Modify: `pyproject.toml` (add `httpx` dependency)

**Interfaces:**
- Produces:
  - `CalendarSyncConfig` dataclass with fields: `enabled: bool`, `interval_seconds: int`, `days_ahead: int`, `outlook_token_file: str`, `caldav_url: str`, `caldav_username: str`, `caldav_password: str`, `state_file: str`
  - `Config.calendar_sync: Optional[CalendarSyncConfig]`
  - `async get_outlook_token(token_file: str) -> str` — returns valid access token or raises `TokenError`
  - `async refresh_outlook_token(token_file: str) -> str` — refreshes and returns new token

- [ ] **Step 1: Add httpx dependency to pyproject.toml**

In `pyproject.toml`, add `httpx` to `dependencies`:

```toml
dependencies = ["pyyaml>=6.0", "rumps>=0.4.0", "httpx>=0.27.0"]
```

- [ ] **Step 2: Write failing test for CalendarSyncConfig**

Create `tests/test_outlook_token.py`:

```python
import pytest
from kanban_agent.config import Config, CalendarSyncConfig


def test_calendar_sync_config_defaults():
    cfg = CalendarSyncConfig(
        caldav_url="https://nas:5232/user/cal/",
        caldav_username="user",
        caldav_password="pass",
    )
    assert cfg.enabled is True
    assert cfg.interval_seconds == 900
    assert cfg.days_ahead == 14
    assert "sap_tokens.json" in cfg.outlook_token_file
    assert "head-nurse" in cfg.state_file


def test_config_loads_calendar_sync_section(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
repo: "user/tasks"
project_number: 1
calendar_sync:
  caldav_url: "https://nas:5232/user/cal/"
  caldav_username: "user"
  caldav_password: "secret"
  interval_seconds: 300
"""
    )
    cfg = Config.load(str(config_file))
    assert cfg.calendar_sync is not None
    assert cfg.calendar_sync.caldav_url == "https://nas:5232/user/cal/"
    assert cfg.calendar_sync.interval_seconds == 300


def test_config_without_calendar_sync(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
repo: "user/tasks"
project_number: 1
"""
    )
    cfg = Config.load(str(config_file))
    assert cfg.calendar_sync is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/I572881/.cline/worktrees/1f890/Head-Nurse && python -m pytest tests/test_outlook_token.py -v`
Expected: FAIL — `CalendarSyncConfig` not defined

- [ ] **Step 4: Implement CalendarSyncConfig and update Config.load**

Modify `src/kanban_agent/config.py`:

```python
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
    max_budget_per_task_usd: float = 1.0
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

        with open(config_path) as f:
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
```

- [ ] **Step 5: Run config tests to verify they pass**

Run: `python -m pytest tests/test_outlook_token.py::test_calendar_sync_config_defaults tests/test_outlook_token.py::test_config_loads_calendar_sync_section tests/test_outlook_token.py::test_config_without_calendar_sync -v`
Expected: 3 PASSED

- [ ] **Step 6: Write failing test for outlook token reader**

Append to `tests/test_outlook_token.py`:

```python
import json
import time

import pytest


@pytest.fixture
def token_file(tmp_path):
    """Create a mock sap_tokens.json."""
    path = tmp_path / "sap_tokens.json"
    storage = {
        "tokens": [
            {
                "token": "access-token-abc",
                "audience": "https://outlook.office.com",
                "expiresAt": int(time.time()) + 3600,
                "scopes": ["Calendars.ReadWrite"],
                "appDisplayName": "outlook-mcp",
            }
        ],
        "_refreshToken": "refresh-token-xyz",
        "timestamp": int(time.time() * 1000),
        "source": "test",
    }
    path.write_text(json.dumps(storage))
    return str(path)


@pytest.fixture
def expired_token_file(tmp_path):
    """Create a mock sap_tokens.json with expired token."""
    path = tmp_path / "sap_tokens.json"
    storage = {
        "tokens": [
            {
                "token": "expired-token",
                "audience": "https://outlook.office.com",
                "expiresAt": int(time.time()) - 100,
                "scopes": ["Calendars.ReadWrite"],
            }
        ],
        "_refreshToken": "refresh-token-xyz",
        "timestamp": int(time.time() * 1000),
        "source": "test",
    }
    path.write_text(json.dumps(storage))
    return str(path)


@pytest.mark.asyncio
async def test_get_outlook_token_valid(token_file):
    from kanban_agent.outlook_token import get_outlook_token

    token = await get_outlook_token(token_file)
    assert token == "access-token-abc"


@pytest.mark.asyncio
async def test_get_outlook_token_missing_file():
    from kanban_agent.outlook_token import get_outlook_token, TokenError

    with pytest.raises(TokenError):
        await get_outlook_token("/nonexistent/path/tokens.json")


@pytest.mark.asyncio
async def test_get_outlook_token_expired_triggers_refresh(expired_token_file, monkeypatch):
    from kanban_agent import outlook_token
    from kanban_agent.outlook_token import get_outlook_token

    async def mock_refresh(token_file):
        return "refreshed-token"

    monkeypatch.setattr(outlook_token, "refresh_outlook_token", mock_refresh)
    token = await get_outlook_token(expired_token_file)
    assert token == "refreshed-token"
```

- [ ] **Step 7: Run token tests to verify they fail**

Run: `python -m pytest tests/test_outlook_token.py::test_get_outlook_token_valid -v`
Expected: FAIL — module `kanban_agent.outlook_token` not found

- [ ] **Step 8: Implement outlook_token.py**

Create `src/kanban_agent/outlook_token.py`:

```python
"""Read and refresh Outlook OAuth tokens from the MCP server's token file."""

import json
import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

TENANT_ID = "69b863e3-480a-4ee9-8bd0-20a8adb6909b"
CLIENT_ID = "9199bf20-a13f-4107-85dc-02114787ef48"
TOKEN_ENDPOINT = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
SCOPES = (
    "offline_access https://outlook.office.com/Mail.ReadWrite "
    "https://outlook.office.com/Mail.Send https://outlook.office.com/Calendars.ReadWrite "
    "https://outlook.office.com/People.Read https://outlook.office.com/User.Read"
)

# Refresh margin: refresh if token expires within 5 minutes
REFRESH_MARGIN_SECONDS = 300


class TokenError(Exception):
    """Raised when a valid token cannot be obtained."""


async def get_outlook_token(token_file: str) -> str:
    """Get a valid Outlook access token. Refreshes if near expiry.

    Args:
        token_file: Path to sap_tokens.json

    Returns:
        Valid access token string

    Raises:
        TokenError: If no valid token can be obtained
    """
    path = Path(token_file)
    if not path.exists():
        raise TokenError(f"Token file not found: {token_file}")

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise TokenError(f"Cannot read token file: {e}")

    tokens = data.get("tokens", [])
    if not tokens:
        raise TokenError("No tokens in token file")

    # Find Outlook token
    outlook_token = None
    for t in tokens:
        audience = t.get("audience", "")
        if "outlook.office" in audience or "graph.microsoft.com" in audience:
            outlook_token = t
            break

    if not outlook_token:
        raise TokenError("No Outlook token found in token file")

    now = time.time()
    expires_at = outlook_token.get("expiresAt", 0)
    remaining = expires_at - now

    # Token still valid with margin
    if remaining > REFRESH_MARGIN_SECONDS:
        return outlook_token["token"]

    # Try refresh
    refresh_token = data.get("_refreshToken")
    if not refresh_token:
        if remaining > 0:
            # Expired soon but no refresh token — use it while it lasts
            return outlook_token["token"]
        raise TokenError("Token expired and no refresh token available")

    logger.info("Outlook token expires in %ds, refreshing...", int(remaining))
    return await refresh_outlook_token(token_file)


async def refresh_outlook_token(token_file: str) -> str:
    """Refresh the Outlook access token using the stored refresh token.

    Args:
        token_file: Path to sap_tokens.json

    Returns:
        New access token string

    Raises:
        TokenError: If refresh fails
    """
    path = Path(token_file)
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise TokenError(f"Cannot read token file for refresh: {e}")

    refresh_token = data.get("_refreshToken")
    if not refresh_token:
        raise TokenError("No refresh token available")

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                TOKEN_ENDPOINT,
                data={
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "scope": SCOPES,
                    "refresh_token": refresh_token,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise TokenError(f"Token refresh request failed: {e}")

    result = resp.json()
    access_token = result.get("access_token")
    if not access_token:
        raise TokenError("Refresh response missing access_token")

    new_refresh = result.get("refresh_token", refresh_token)
    expires_in = result.get("expires_in", 3600)

    # Write back to token file (atomic-ish with fsync)
    new_storage = {
        "tokens": [
            {
                "token": access_token,
                "audience": "https://outlook.office.com",
                "expiresAt": int(time.time()) + expires_in,
                "scopes": SCOPES.split(" "),
                "appDisplayName": "outlook-mcp",
            }
        ],
        "_refreshToken": new_refresh,
        "timestamp": int(time.time() * 1000),
        "source": "head-nurse-refresh",
    }

    import os
    content = json.dumps(new_storage, indent=2)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        os.write(fd, content.encode())
        os.fsync(fd)
    finally:
        os.close(fd)

    logger.info("Token refreshed successfully (expires in %ds)", expires_in)
    return access_token
```

- [ ] **Step 9: Install test dependencies and run all token tests**

Run:
```bash
cd /Users/I572881/.cline/worktrees/1f890/Head-Nurse
pip install -e ".[dev]" httpx pytest-asyncio
python -m pytest tests/test_outlook_token.py -v
```
Expected: All 5 tests PASS

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml src/kanban_agent/config.py src/kanban_agent/outlook_token.py tests/test_outlook_token.py
git commit -m "feat(calendar-sync): add config extension and outlook token reader"
```

---

### Task 2: Calendar sync core logic

**Files:**
- Create: `src/kanban_agent/calendar_sync.py`
- Create: `tests/test_calendar_sync.py`
- Modify: `pyproject.toml` (add `icalendar`, `caldav` dependencies)

**Interfaces:**
- Consumes:
  - `CalendarSyncConfig` from `config.py`
  - `async get_outlook_token(token_file: str) -> str` from `outlook_token.py`
- Produces:
  - `CalendarSync(config: CalendarSyncConfig)` class with:
    - `async start() -> None` — starts the periodic sync loop
    - `async stop() -> None` — cancels the loop
    - `async sync_once() -> SyncResult` — runs one sync cycle
    - `last_sync_time: Optional[str]` — property for menubar display
  - `SyncResult(created: int, updated: int, deleted: int, error: Optional[str])`

- [ ] **Step 1: Add icalendar and caldav dependencies**

In `pyproject.toml`:

```toml
dependencies = ["pyyaml>=6.0", "rumps>=0.4.0", "httpx>=0.27.0", "icalendar>=5.0", "caldav>=1.3"]
```

- [ ] **Step 2: Write failing test for sync_once with mocked APIs**

Create `tests/test_calendar_sync.py`:

```python
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kanban_agent.config import CalendarSyncConfig


@pytest.fixture
def sync_config(tmp_path):
    token_file = tmp_path / "sap_tokens.json"
    token_file.write_text(json.dumps({
        "tokens": [{
            "token": "test-token",
            "audience": "https://outlook.office.com",
            "expiresAt": int(time.time()) + 3600,
            "scopes": ["Calendars.ReadWrite"],
        }],
        "_refreshToken": "test-refresh",
        "timestamp": int(time.time() * 1000),
        "source": "test",
    }))
    state_file = tmp_path / "state.json"
    return CalendarSyncConfig(
        caldav_url="https://nas:5232/user/cal/",
        caldav_username="user",
        caldav_password="pass",
        outlook_token_file=str(token_file),
        state_file=str(state_file),
    )


MOCK_OUTLOOK_EVENTS = [
    {
        "Id": "event-001",
        "Subject": "Team Standup",
        "Start": {"DateTime": "2026-06-25T09:00:00.0000000", "TimeZone": "UTC"},
        "End": {"DateTime": "2026-06-25T09:30:00.0000000", "TimeZone": "UTC"},
        "Location": {"DisplayName": "Room 42"},
        "BodyPreview": "Daily standup meeting",
        "IsAllDay": False,
        "IsCancelled": False,
        "LastModifiedDateTime": "2026-06-24T10:00:00Z",
    },
    {
        "Id": "event-002",
        "Subject": "All Hands",
        "Start": {"DateTime": "2026-06-26T00:00:00.0000000", "TimeZone": "UTC"},
        "End": {"DateTime": "2026-06-27T00:00:00.0000000", "TimeZone": "UTC"},
        "Location": {"DisplayName": ""},
        "BodyPreview": "",
        "IsAllDay": True,
        "IsCancelled": False,
        "LastModifiedDateTime": "2026-06-24T12:00:00Z",
    },
]


@pytest.mark.asyncio
async def test_sync_once_creates_new_events(sync_config):
    from kanban_agent.calendar_sync import CalendarSync

    sync = CalendarSync(sync_config)

    # Mock Outlook API response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"value": MOCK_OUTLOOK_EVENTS}
    mock_response.raise_for_status = MagicMock()

    # Mock CalDAV client
    mock_calendar = MagicMock()
    mock_calendar.save_event = MagicMock()

    with patch("kanban_agent.calendar_sync.httpx.AsyncClient") as mock_client_cls, \
         patch("kanban_agent.calendar_sync._get_caldav_calendar", return_value=mock_calendar):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = await sync.sync_once()

    assert result.created == 2
    assert result.updated == 0
    assert result.deleted == 0
    assert result.error is None
    assert mock_calendar.save_event.call_count == 2


@pytest.mark.asyncio
async def test_sync_once_deletes_removed_events(sync_config):
    from kanban_agent.calendar_sync import CalendarSync

    # Pre-populate state with an event that's no longer in Outlook
    state = {
        "last_sync": "2026-06-24T10:00:00Z",
        "events": {
            "event-gone": {
                "uid": "outlook-event-gone",
                "last_modified": "2026-06-24T08:00:00Z",
            }
        },
    }
    from pathlib import Path
    Path(sync_config.state_file).write_text(json.dumps(state))

    sync = CalendarSync(sync_config)

    # Mock Outlook returns empty
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"value": []}
    mock_response.raise_for_status = MagicMock()

    # Mock CalDAV
    mock_event_obj = MagicMock()
    mock_calendar = MagicMock()
    mock_calendar.save_event = MagicMock()
    mock_calendar.event_by_uid = MagicMock(return_value=mock_event_obj)

    with patch("kanban_agent.calendar_sync.httpx.AsyncClient") as mock_client_cls, \
         patch("kanban_agent.calendar_sync._get_caldav_calendar", return_value=mock_calendar):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = await sync.sync_once()

    assert result.deleted == 1
    mock_event_obj.delete.assert_called_once()


@pytest.mark.asyncio
async def test_sync_once_updates_modified_events(sync_config):
    from kanban_agent.calendar_sync import CalendarSync

    # Pre-populate state with event-001 at older timestamp
    state = {
        "last_sync": "2026-06-24T09:00:00Z",
        "events": {
            "event-001": {
                "uid": "outlook-event-001",
                "last_modified": "2026-06-23T10:00:00Z",  # older than mock
            }
        },
    }
    from pathlib import Path
    Path(sync_config.state_file).write_text(json.dumps(state))

    sync = CalendarSync(sync_config)

    # Mock Outlook returns event-001 only (with newer LastModifiedDateTime)
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"value": [MOCK_OUTLOOK_EVENTS[0]]}
    mock_response.raise_for_status = MagicMock()

    mock_calendar = MagicMock()
    mock_calendar.save_event = MagicMock()

    with patch("kanban_agent.calendar_sync.httpx.AsyncClient") as mock_client_cls, \
         patch("kanban_agent.calendar_sync._get_caldav_calendar", return_value=mock_calendar):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = await sync.sync_once()

    assert result.created == 0
    assert result.updated == 1
    assert result.deleted == 0
    mock_calendar.save_event.assert_called_once()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_calendar_sync.py -v`
Expected: FAIL — module `kanban_agent.calendar_sync` not found

- [ ] **Step 4: Implement calendar_sync.py**

Create `src/kanban_agent/calendar_sync.py`:

```python
"""Outlook → CalDAV one-way calendar sync."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Optional

import httpx
from icalendar import Calendar, Event

from .config import CalendarSyncConfig
from .outlook_token import TokenError, get_outlook_token

logger = logging.getLogger(__name__)

OUTLOOK_API_BASE = "https://outlook.office.com/api/v2.0"


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    error: Optional[str] = None


def _get_caldav_calendar(url: str, username: str, password: str):
    """Connect to CalDAV and return the calendar object.

    Uses the caldav library (sync), intended to be called via run_in_executor.
    """
    import caldav

    client = caldav.DAVClient(url=url, username=username, password=password)
    principal = client.principal()
    # If URL points directly to a calendar, use it; otherwise get first calendar
    try:
        calendar = caldav.Calendar(client=client, url=url)
        # Verify it exists by fetching properties
        calendar.get_properties([])
        return calendar
    except Exception:
        calendars = principal.calendars()
        if not calendars:
            raise RuntimeError("No calendars found on CalDAV server")
        return calendars[0]


def _build_vevent(event: dict) -> str:
    """Convert an Outlook event dict to iCalendar VEVENT string."""
    cal = Calendar()
    cal.add("prodid", "-//Head-Nurse//CalendarSync//EN")
    cal.add("version", "2.0")

    vevent = Event()
    event_id = event["Id"]
    vevent.add("uid", f"outlook-{event_id}")
    vevent.add("summary", event.get("Subject", "(No Subject)"))

    start_dt = event["Start"]["DateTime"]
    end_dt = event["End"]["DateTime"]
    is_all_day = event.get("IsAllDay", False)

    if is_all_day:
        # All-day events use DATE format
        from datetime import date as date_type
        start_date = datetime.fromisoformat(start_dt.rstrip("0").rstrip(".")).date()
        end_date = datetime.fromisoformat(end_dt.rstrip("0").rstrip(".")).date()
        vevent.add("dtstart", start_date)
        vevent.add("dtend", end_date)
    else:
        start = datetime.fromisoformat(start_dt.rstrip("0").rstrip(".")).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(end_dt.rstrip("0").rstrip(".")).replace(tzinfo=timezone.utc)
        vevent.add("dtstart", start)
        vevent.add("dtend", end)

    location = event.get("Location", {}).get("DisplayName", "")
    if location:
        vevent.add("location", location)

    description = event.get("BodyPreview", "")
    if description:
        vevent.add("description", description)

    vevent.add("dtstamp", datetime.now(timezone.utc))

    cal.add_component(vevent)
    return cal.to_ical().decode("utf-8")


class _SyncState:
    """Manages the local sync state file."""

    def __init__(self, state_file: str):
        self._path = Path(state_file)
        self._data: dict = {"last_sync": None, "events": {}}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {"last_sync": None, "events": {}}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(self._data, indent=2)
        self._path.write_text(content)

    @property
    def events(self) -> dict:
        return self._data.get("events", {})

    def set_event(self, outlook_id: str, uid: str, last_modified: str) -> None:
        self._data["events"][outlook_id] = {
            "uid": uid,
            "last_modified": last_modified,
        }

    def remove_event(self, outlook_id: str) -> None:
        self._data["events"].pop(outlook_id, None)

    def set_last_sync(self, ts: str) -> None:
        self._data["last_sync"] = ts


class CalendarSync:
    """Periodically syncs Outlook calendar events to CalDAV."""

    def __init__(self, config: CalendarSyncConfig):
        self._config = config
        self._task: Optional[asyncio.Task] = None
        self._last_sync_time: Optional[str] = None
        self._state = _SyncState(config.state_file)

    @property
    def last_sync_time(self) -> Optional[str]:
        return self._last_sync_time

    async def start(self) -> None:
        """Start the periodic sync loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Calendar sync started (interval=%ds, days_ahead=%d)",
            self._config.interval_seconds,
            self._config.days_ahead,
        )

    async def stop(self) -> None:
        """Stop the periodic sync loop."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("Calendar sync stopped")

    async def _loop(self) -> None:
        """Run sync_once every interval_seconds."""
        while True:
            try:
                result = await self.sync_once()
                if result.error:
                    logger.warning("Calendar sync error: %s", result.error)
                else:
                    logger.info(
                        "Calendar sync: %d created, %d updated, %d deleted",
                        result.created, result.updated, result.deleted,
                    )
            except Exception:
                logger.exception("Calendar sync unexpected error")

            await asyncio.sleep(self._config.interval_seconds)

    async def sync_once(self) -> SyncResult:
        """Run a single sync cycle."""
        # 1. Get token
        try:
            token = await get_outlook_token(self._config.outlook_token_file)
        except TokenError as e:
            return SyncResult(error=str(e))

        # 2. Fetch Outlook events
        now = datetime.now(timezone.utc)
        start_dt = now.strftime("%Y-%m-%dT00:00:00Z")
        end_dt = (now + timedelta(days=self._config.days_ahead)).strftime("%Y-%m-%dT23:59:59Z")

        url = (
            f"{OUTLOOK_API_BASE}/me/calendarview"
            f"?startdatetime={start_dt}&enddatetime={end_dt}"
            f"&$top=200&$orderby=Start/DateTime asc"
            f"&$select=Id,Subject,Start,End,Location,BodyPreview,IsAllDay,IsCancelled,LastModifiedDateTime"
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
        except httpx.HTTPError as e:
            return SyncResult(error=f"Outlook API error: {e}")

        outlook_events = resp.json().get("value", [])

        # 3. Get CalDAV calendar (sync lib → run in executor)
        loop = asyncio.get_running_loop()
        try:
            calendar = await loop.run_in_executor(
                None,
                partial(
                    _get_caldav_calendar,
                    self._config.caldav_url,
                    self._config.caldav_username,
                    self._config.caldav_password,
                ),
            )
        except Exception as e:
            return SyncResult(error=f"CalDAV connection error: {e}")

        # 4. Diff and sync
        result = SyncResult()
        current_ids = set()

        for event in outlook_events:
            event_id = event["Id"]

            # Skip cancelled events (treat as delete)
            if event.get("IsCancelled", False):
                continue

            current_ids.add(event_id)
            last_modified = event.get("LastModifiedDateTime", "")
            existing = self._state.events.get(event_id)

            if existing is None:
                # New event → create
                try:
                    ics = _build_vevent(event)
                    await loop.run_in_executor(None, calendar.save_event, ics)
                    self._state.set_event(event_id, f"outlook-{event_id}", last_modified)
                    result.created += 1
                except Exception:
                    logger.warning("Failed to create event %s", event_id, exc_info=True)
            elif existing.get("last_modified") != last_modified:
                # Modified → update (PUT with same UID overwrites)
                try:
                    ics = _build_vevent(event)
                    await loop.run_in_executor(None, calendar.save_event, ics)
                    self._state.set_event(event_id, f"outlook-{event_id}", last_modified)
                    result.updated += 1
                except Exception:
                    logger.warning("Failed to update event %s", event_id, exc_info=True)

        # 5. Delete events no longer in Outlook
        state_ids = set(self._state.events.keys())
        for gone_id in state_ids - current_ids:
            uid = self._state.events[gone_id]["uid"]
            try:
                event_obj = await loop.run_in_executor(None, calendar.event_by_uid, uid)
                await loop.run_in_executor(None, event_obj.delete)
                self._state.remove_event(gone_id)
                result.deleted += 1
            except Exception:
                logger.warning("Failed to delete event %s (uid=%s)", gone_id, uid, exc_info=True)

        # 6. Persist state
        self._state.set_last_sync(now.isoformat())
        self._state.save()
        self._last_sync_time = now.strftime("%H:%M")

        return result
```

- [ ] **Step 5: Install new dependencies and run tests**

Run:
```bash
pip install icalendar caldav
python -m pytest tests/test_calendar_sync.py -v
```
Expected: All 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/kanban_agent/calendar_sync.py tests/test_calendar_sync.py
git commit -m "feat(calendar-sync): implement core sync logic with diff and CalDAV write"
```

---

### Task 3: Menubar integration

**Files:**
- Modify: `src/kanban_agent/menubar.py`
- Modify: `config.example.yaml`

**Interfaces:**
- Consumes:
  - `CalendarSync(config: CalendarSyncConfig)` from `calendar_sync.py`
  - `CalendarSync.start() -> None`
  - `CalendarSync.stop() -> None`
  - `CalendarSync.last_sync_time: Optional[str]`
  - `Config.calendar_sync: Optional[CalendarSyncConfig]` from `config.py`

- [ ] **Step 1: Modify menubar.py to start CalendarSync**

Update `src/kanban_agent/menubar.py` — full replacement:

```python
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
from .calendar_sync import CalendarSync
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
            "calendar_row",
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
        self._calendar_sync: Optional[CalendarSync] = None
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
        m["calendar_row"].title = "📅 Calendar: --"
        m["calendar_row"].set_callback(None)  # disabled
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

        # Create CalendarSync if configured
        if config.calendar_sync and config.calendar_sync.enabled:
            self._calendar_sync = CalendarSync(config.calendar_sync)

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

            # Start calendar sync in the same loop
            if self._calendar_sync:
                loop.run_until_complete(self._calendar_sync.start())

            loop.run_until_complete(self._agent.run())
        except Exception:
            logger.exception("Agent crashed")
            if self._agent is not None:
                self._agent._set_status(state="crashed", error="see logs")
        finally:
            # Stop calendar sync
            if self._calendar_sync:
                try:
                    loop.run_until_complete(self._calendar_sync.stop())
                except Exception:
                    pass

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
        self._calendar_sync = None
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

        # Update calendar sync status
        if self._calendar_sync and self._calendar_sync.last_sync_time:
            m["calendar_row"].title = f"📅 Calendar: synced {self._calendar_sync.last_sync_time}"
        elif self._calendar_sync:
            m["calendar_row"].title = "📅 Calendar: pending"
        else:
            m["calendar_row"].title = "📅 Calendar: off"

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
        if path.exists():
            subprocess.run(["open", str(path)], check=False)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["open", str(path.parent)], check=False)

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

- [ ] **Step 2: Update config.example.yaml**

Append to `config.example.yaml`:

```yaml

# ── Outlook → CalDAV Calendar Sync ──────────────────────────────────────
# Syncs Outlook calendar events to a CalDAV server (one-way mirror)
calendar_sync:
  enabled: false
  interval_seconds: 900          # 15 minutes
  days_ahead: 14

  # Outlook MCP token path (reuses existing OAuth tokens)
  outlook_token_file: "~/.sap-mcp/cookies/outlook/sap_tokens.json"

  # CalDAV target (e.g., Radicale on NAS)
  caldav_url: "https://your-nas:5232/user/calendar/"
  caldav_username: "user"
  caldav_password: "pass"         # or ${CALDAV_PASSWORD} for env var

  # State persistence
  state_file: "~/.local/state/head-nurse/calendar-sync-state.json"
```

- [ ] **Step 3: Run existing tests to ensure nothing broke**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 4: Commit**

```bash
git add src/kanban_agent/menubar.py config.example.yaml
git commit -m "feat(calendar-sync): integrate into menubar app with status display"
```

---

### Task 4: End-to-end manual verification and docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README with calendar sync section**

Add after the "## Features" section in `README.md`:

```markdown
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
```

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add calendar sync setup and usage to README"
```
