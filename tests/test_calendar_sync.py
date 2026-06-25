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
    mock_response.json = MagicMock(return_value={"value": MOCK_OUTLOOK_EVENTS})
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
    mock_response.json = MagicMock(return_value={"value": []})
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
    mock_response.json = MagicMock(return_value={"value": [MOCK_OUTLOOK_EVENTS[0]]})
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
