# Outlook → CalDAV Calendar Sync

**Date:** 2026-06-25  
**Status:** Approved  
**Module:** `calendar_sync`

## Summary

Add a new module to Head-Nurse that syncs Outlook calendar events to a self-hosted CalDAV (Radicale) server on NAS. One-way sync, running every 15 minutes, integrated into the existing menubar app process.

## Requirements

- **Direction:** Outlook → CalDAV (one-way, CalDAV is a read-only mirror)
- **Frequency:** Every 15 minutes (configurable)
- **Time range:** Today + 14 days ahead
- **Deletion:** Sync deletes — events removed/cancelled in Outlook are deleted from CalDAV
- **Integration:** Runs inside Head-Nurse process, shares asyncio event loop
- **Independence:** Sync failures must not affect the kanban agent

## Architecture

```
┌─────────────────────────────────────────────┐
│              Head-Nurse Process              │
│                                             │
│  ┌───────────────┐   ┌──────────────────┐  │
│  │  KanbanAgent  │   │  CalendarSync    │  │
│  │  (existing)   │   │  (new module)    │  │
│  └───────────────┘   └──────────────────┘  │
│         │                     │             │
│    asyncio event loop (shared)              │
└─────────────────────────────────────────────┘
          │                     │
          ▼                     ▼
   GitHub Project V2    ┌──────────────┐
                        │ Outlook API  │ ← token from ~/.sap-mcp/cookies/outlook/
                        └──────┬───────┘
                               ▼
                        ┌──────────────┐
                        │   Radicale   │ (NAS CalDAV)
                        └──────────────┘
```

## Authentication

Reuses the Outlook MCP server's OAuth token file directly:

- **Token path:** `~/.sap-mcp/cookies/outlook/sap_tokens.json`
- **Format:** `TokenStorage` with `tokens[]` array; pick entry where `audience` includes `outlook.office.com`
- **Refresh:** If `expiresAt` is within 5 minutes of now, use `_refreshToken` to get a new token via the same OAuth2 token endpoint (tenant `69b863e3-480a-4ee9-8bd0-20a8adb6909b`, client `9199bf20-a13f-4107-85dc-02114787ef48`)
- **Write-back:** After refresh, write updated token back to the same file (atomic write with fsync) so Outlook MCP server stays in sync

## Sync Logic

Each 15-minute cycle:

1. Load OAuth token (refresh if needed)
2. Call `GET https://outlook.office.com/api/v2.0/me/calendarview` with `startdatetime=today` and `enddatetime=today+14d`
3. Load state file to get previously synced event IDs
4. Compute diff:
   - **New events** (in Outlook, not in state) → CalDAV PUT (create)
   - **Modified events** (in Outlook, lastModified changed) → CalDAV PUT (update)
   - **Deleted events** (in state, not in Outlook response) → CalDAV DELETE
5. Update and persist state file

## State File

**Path:** `~/.local/state/head-nurse/calendar-sync-state.json`

```json
{
  "last_sync": "2026-06-25T10:00:00Z",
  "events": {
    "outlook-event-id-1": {
      "uid": "outlook-outlook-event-id-1",
      "last_modified": "2026-06-25T08:30:00Z"
    }
  }
}
```

## Event Mapping

| Outlook Field | iCalendar VEVENT Field |
|---------------|----------------------|
| Id | UID (prefixed: `outlook-{Id}`) |
| Subject | SUMMARY |
| Start.DateTime | DTSTART |
| End.DateTime | DTEND |
| Location.DisplayName | LOCATION |
| BodyPreview | DESCRIPTION |
| IsAllDay=true | DTSTART/DTEND use DATE format |
| IsCancelled=true | Triggers DELETE from CalDAV |

## Configuration

New `calendar_sync` section in `~/.config/kanban-agent/config.yaml`:

```yaml
calendar_sync:
  enabled: true
  interval_seconds: 900
  days_ahead: 14
  outlook_token_file: "~/.sap-mcp/cookies/outlook/sap_tokens.json"
  caldav_url: "https://nas-host:5232/user/calendar/"
  caldav_username: "user"
  caldav_password: "pass"
  state_file: "~/.local/state/head-nurse/calendar-sync-state.json"
```

- `enabled: false` disables the module entirely (zero impact on existing functionality)
- `caldav_password` supports `${ENV_VAR}` syntax for environment variable references

## Menubar Integration

New menu item showing last sync time:

```
☁️ HeadNurse
├── Start / Stop
├── Calendar: synced 10:15    ← NEW
├── Open Config
├── View Log
└── Quit
```

## Error Handling

| Scenario | Action |
|----------|--------|
| Token expired, refresh fails | Log WARNING, retry next cycle |
| CalDAV server unreachable | Log ERROR, retry next cycle |
| Single event write fails | Skip event, continue rest, log event ID |
| Network timeout | 30s per request, treat as failure |

Sync errors never crash the process or affect the kanban agent.

## Logging

- **INFO:** `"Calendar sync: 3 created, 1 updated, 0 deleted"` (every successful cycle)
- **WARNING/ERROR:** Specific failure details with event IDs

## New Dependencies

| Package | Purpose |
|---------|---------|
| `httpx` | Async HTTP client for Outlook API calls |
| `icalendar` | Generate iCalendar (.ics) VEVENT content |
| `caldav` | Python CalDAV client for Radicale writes |

## File Structure

```
src/kanban_agent/
├── calendar_sync.py      ← NEW: CalendarSync class, sync loop, diff logic
├── outlook_token.py      ← NEW: Token reader + refresh (from sap_tokens.json)
├── agent.py              (unchanged)
├── menubar.py            (add CalendarSync startup + menu item)
├── config.py             (add calendar_sync config parsing)
└── ...
```

## Out of Scope

- Bidirectional sync (CalDAV → Outlook)
- Recurring event expansion (sync instances as-is from calendarView)
- Attendee/reminder sync
- Multiple calendar support (single calendar target only)
