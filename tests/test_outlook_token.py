import json
import time

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
