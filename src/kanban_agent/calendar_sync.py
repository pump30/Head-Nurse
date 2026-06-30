"""Outlook → CalDAV one-way calendar sync."""

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from icalendar import Calendar, Event

from .config import CalendarSyncConfig
from .device_code_flow import DeviceCodeFlowManager, DeviceFlowState
from .outlook_token import TokenError, get_outlook_token

logger = logging.getLogger(__name__)

OUTLOOK_API_BASE = "https://outlook.office.com/api/v2.0"
SEND_EMAIL_SCRIPT = Path.home() / ".claude" / "skills" / "send-email" / "scripts" / "send.py"


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    error: Optional[str] = None
    notified_events: list = field(default_factory=list)


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
        start_date = datetime.fromisoformat(start_dt).date()
        end_date = datetime.fromisoformat(end_dt).date()
        vevent.add("dtstart", start_date)
        vevent.add("dtend", end_date)
    else:
        start = datetime.fromisoformat(start_dt).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(end_dt).replace(tzinfo=timezone.utc)
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


def _send_new_event_email(events: list[dict]) -> None:
    """Send email notification for newly synced calendar events."""
    if not events or not SEND_EMAIL_SCRIPT.exists():
        return

    lines = []
    for ev in events:
        subject = ev.get("Subject", "(No Subject)")
        start_str = ev["Start"]["DateTime"][:16].replace("T", " ")
        location = ev.get("Location", {}).get("DisplayName", "")
        line = f"• {start_str} — {subject}"
        if location:
            line += f" @ {location}"
        lines.append(line)

    body = "以下新日程已同步到你的日历：\n\n" + "\n".join(lines)
    subject = f"📅 新日程: {events[0].get('Subject', 'New Event')}"
    if len(events) > 1:
        subject = f"📅 {len(events)} 个新日程已同步"

    try:
        subprocess.run(
            [
                "python3", str(SEND_EMAIL_SCRIPT),
                "--subject", subject,
                "--body", body,
            ],
            check=False,
            capture_output=True,
            timeout=15,
        )
        logger.info("Sent email notification for %d new event(s)", len(events))
    except Exception:
        logger.warning("Failed to send new event email", exc_info=True)


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
        self._device_flow = DeviceCodeFlowManager(config.outlook_token_file)

    @property
    def last_sync_time(self) -> Optional[str]:
        return self._last_sync_time

    @property
    def auth_state(self) -> DeviceFlowState:
        """Current device code flow state (polled by menubar)."""
        return self._device_flow.state

    @property
    def auth_user_code(self) -> Optional[str]:
        """User code to display during device flow auth."""
        return self._device_flow.user_code

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

    async def _caldav_put(self, client: httpx.AsyncClient, uid: str, ics: str) -> None:
        """PUT an iCalendar event to CalDAV server."""
        url = f"{self._config.caldav_url}{uid}.ics"
        resp = await client.put(
            url,
            content=ics.encode("utf-8"),
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
        # 201 Created or 204 No Content are both success
        if resp.status_code not in (200, 201, 204):
            resp.raise_for_status()

    async def _caldav_delete(self, client: httpx.AsyncClient, uid: str) -> None:
        """DELETE an iCalendar event from CalDAV server."""
        url = f"{self._config.caldav_url}{uid}.ics"
        resp = await client.delete(url)
        # 204 No Content or 404 Not Found (already gone) are both fine
        if resp.status_code not in (200, 204, 404):
            resp.raise_for_status()

    async def sync_once(self) -> SyncResult:
        """Run a single sync cycle."""
        # 1. Get token
        try:
            token = await get_outlook_token(self._config.outlook_token_file)
        except TokenError as e:
            # Token refresh failed — initiate device code flow as fallback
            if not self._device_flow.is_in_progress:
                logger.warning(
                    "Token refresh failed, initiating device code flow: %s", e
                )
                await self._device_flow.initiate()
            return SyncResult(error=f"Auth needed: {e}")

        # Token obtained successfully — reset any stale flow state
        if self._device_flow.state in (
            DeviceFlowState.SUCCESS,
            DeviceFlowState.FAILED,
            DeviceFlowState.EXPIRED,
        ):
            self._device_flow.reset()

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
            outlook_events = []
            async with httpx.AsyncClient(timeout=30.0) as client:
                next_url: Optional[str] = url
                while next_url:
                    resp = await client.get(
                        next_url,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    outlook_events.extend(data.get("value", []))
                    next_url = data.get("@odata.nextLink")
        except httpx.HTTPError as e:
            return SyncResult(error=f"Outlook API error: {e}")

        # 3. Connect to CalDAV via httpx (Basic Auth)
        import base64
        caldav_auth = base64.b64encode(
            f"{self._config.caldav_username}:{self._config.caldav_password}".encode()
        ).decode()
        caldav_headers = {"Authorization": f"Basic {caldav_auth}"}

        # 4. Diff and sync
        result = SyncResult()
        current_ids = set()
        failures = 0
        new_events: list[dict] = []

        async with httpx.AsyncClient(timeout=30.0, headers=caldav_headers) as dav_client:
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
                        await self._caldav_put(dav_client, f"outlook-{event_id}", ics)
                        self._state.set_event(event_id, f"outlook-{event_id}", last_modified)
                        result.created += 1
                        new_events.append(event)
                    except Exception:
                        failures += 1
                        logger.warning("Failed to create event %s", event_id, exc_info=True)
                elif existing.get("last_modified") != last_modified:
                    # Modified → update (PUT with same UID overwrites)
                    try:
                        ics = _build_vevent(event)
                        await self._caldav_put(dav_client, f"outlook-{event_id}", ics)
                        self._state.set_event(event_id, f"outlook-{event_id}", last_modified)
                        result.updated += 1
                    except Exception:
                        failures += 1
                        logger.warning("Failed to update event %s", event_id, exc_info=True)

            # 5. Delete events no longer in Outlook
            state_ids = set(self._state.events.keys())
            for gone_id in state_ids - current_ids:
                uid = self._state.events[gone_id]["uid"]
                try:
                    await self._caldav_delete(dav_client, uid)
                    self._state.remove_event(gone_id)
                    result.deleted += 1
                except Exception:
                    failures += 1
                    logger.warning("Failed to delete event %s (uid=%s)", gone_id, uid, exc_info=True)

        # 6. Persist state and update sync time only if no failures
        if failures > 0:
            result.error = f"CalDAV: {failures} operations failed"
            # Still save state for partial successes
            self._state.save()
        else:
            self._state.set_last_sync(now.isoformat())
            self._state.save()
            self._last_sync_time = datetime.now().strftime("%H:%M")

        # 7. Send email for newly created events
        if new_events:
            _send_new_event_email(new_events)
            result.notified_events = new_events

        return result
