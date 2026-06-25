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
