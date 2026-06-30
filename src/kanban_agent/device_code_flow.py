"""Outlook re-authentication via Playwright browser token extraction.

When the refresh token expires or is revoked, this module opens a browser
(system Chrome via Playwright) to Outlook Web, extracts MSAL tokens from
localStorage (including refresh_token), and saves them. Same approach as
the Outlook MCP server's extractTokenFromBrowser.
"""

import asyncio
import json
import logging
import os
import tempfile
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from .outlook_token import SCOPES

logger = logging.getLogger(__name__)

# Timeout for waiting for the user to complete browser auth
AUTH_TIMEOUT_SECONDS = 120  # 2 minutes


class DeviceFlowState(Enum):
    """State machine for an auth flow attempt."""

    IDLE = "idle"
    AWAITING_USER = "awaiting_user"
    POLLING = "polling"
    SUCCESS = "success"
    FAILED = "failed"
    EXPIRED = "expired"


class DeviceCodeFlowManager:
    """Manages re-authentication by opening a browser and extracting tokens.

    Uses Playwright to open Outlook Web with system Chrome (SSO-enabled),
    waits for login, then extracts MSAL access + refresh tokens from
    localStorage — same technique as the Outlook MCP server.

    The menubar polls .state from the main thread.
    """

    def __init__(self, token_file: str) -> None:
        self._token_file = token_file
        self._state = DeviceFlowState.IDLE
        self._task: Optional[asyncio.Task] = None
        self._user_code: Optional[str] = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> DeviceFlowState:
        return self._state

    @property
    def is_in_progress(self) -> bool:
        return self._state in (DeviceFlowState.AWAITING_USER, DeviceFlowState.POLLING)

    @property
    def user_code(self) -> Optional[str]:
        """Status text shown in the menu."""
        return self._user_code

    def get_auth_url(self) -> str:
        """Not used for browser flow; returns Outlook URL for manual fallback."""
        return "https://outlook.office.com/mail/"

    async def initiate(self) -> bool:
        """Start the auth flow. Returns False if one is already running."""
        async with self._lock:
            if self.is_in_progress:
                logger.debug("Auth flow already in progress, skipping")
                return False
            self._state = DeviceFlowState.AWAITING_USER
            self._user_code = "opening browser..."

        self._task = asyncio.create_task(self._run_flow())
        return True

    async def _run_flow(self) -> None:
        """Open browser, extract tokens, save."""
        try:
            result = await asyncio.to_thread(self._extract_token_from_browser)

            if result is None:
                if self._state == DeviceFlowState.AWAITING_USER:
                    self._state = DeviceFlowState.FAILED
                    self._user_code = None
                return

            self._state = DeviceFlowState.POLLING
            self._user_code = "saving..."

            self._save_tokens(result)
            self._state = DeviceFlowState.SUCCESS
            self._user_code = None
            logger.info("Browser token extraction completed successfully")

        except Exception:
            logger.exception("Auth flow unexpected error")
            self._state = DeviceFlowState.FAILED
            self._user_code = None

    def _extract_token_from_browser(self) -> Optional[dict]:
        """Launch browser, navigate to Outlook, extract MSAL tokens.

        Runs in a thread (blocking). Returns dict with access_token,
        refresh_token, expires_in or None on failure.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )
            self._state = DeviceFlowState.FAILED
            self._user_code = None
            return None

        chrome_profile = str(
            Path.home() / ".sap-mcp" / "chrome-profile"
        )

        self._user_code = "sign in in browser..."

        try:
            with sync_playwright() as p:
                logger.info("Launching Chrome for Outlook token extraction...")

                context = p.chromium.launch_persistent_context(
                    chrome_profile,
                    headless=False,
                    channel="chrome",
                    args=["--disable-blink-features=AutomationControlled"],
                )

                page = context.pages[0] if context.pages else context.new_page()
                page.goto(
                    "https://outlook.office.com/mail/",
                    wait_until="domcontentloaded",
                )

                logger.info("Waiting for Outlook to load (sign in if prompted)...")
                self._user_code = "waiting for login..."

                # Wait for Outlook mail page to load
                page.wait_for_function(
                    """() => {
                        const url = window.location.href;
                        const isMailUrl = url.includes('outlook.office.com/mail') ||
                                          url.includes('outlook.office365.com/mail');
                        const titleOk = document.title.includes('Mail') ||
                                        document.title.includes('Inbox') ||
                                        document.title.includes('Outlook');
                        return isMailUrl && titleOk;
                    }""",
                    timeout=AUTH_TIMEOUT_SECONDS * 1000,
                )

                logger.info("Outlook loaded, extracting MSAL tokens...")
                self._user_code = "extracting token..."

                # Wait a bit for MSAL to hydrate localStorage
                page.wait_for_timeout(3000)

                # Extract valid MSAL token from localStorage
                result = page.evaluate(
                    """() => {
                        const nowSec = Math.floor(Date.now() / 1000);
                        let bestAt = null;
                        let bestExp = 0;
                        let rt = null;
                        for (let i = 0; i < localStorage.length; i++) {
                            const k = localStorage.key(i);
                            try {
                                const v = JSON.parse(localStorage.getItem(k));
                                if (v.credentialType === 'AccessToken' && v.secret) {
                                    let exp = 0;
                                    try { exp = JSON.parse(atob(v.secret.split('.')[1])).exp; }
                                    catch {}
                                    if (exp > 0 && exp < nowSec + 60) continue;
                                    if (k.includes('outlook.office') || exp > bestExp) {
                                        bestAt = v;
                                        bestExp = exp;
                                    }
                                }
                                if (v.credentialType === 'RefreshToken') rt = v;
                            } catch {}
                        }
                        if (!bestAt || !bestAt.secret) return null;
                        return {
                            access_token: bestAt.secret,
                            refresh_token: rt ? rt.secret : '',
                            expires_at: bestExp,
                            scopes: bestAt.target ? bestAt.target.split(' ') : [],
                        };
                    }"""
                )

                # If no token found, reload and retry (MSAL may not have hydrated)
                if not result or not result.get("access_token"):
                    logger.info("No token on first try, reloading page...")
                    page.reload(wait_until="domcontentloaded")
                    page.wait_for_timeout(5000)
                    result = page.evaluate(
                        """() => {
                            const nowSec = Math.floor(Date.now() / 1000);
                            let bestAt = null;
                            let bestExp = 0;
                            let rt = null;
                            for (let i = 0; i < localStorage.length; i++) {
                                const k = localStorage.key(i);
                                try {
                                    const v = JSON.parse(localStorage.getItem(k));
                                    if (v.credentialType === 'AccessToken' && v.secret) {
                                        let exp = 0;
                                        try { exp = JSON.parse(atob(v.secret.split('.')[1])).exp; }
                                        catch {}
                                        if (exp > 0 && exp < nowSec + 60) continue;
                                        if (k.includes('outlook.office') || exp > bestExp) {
                                            bestAt = v;
                                            bestExp = exp;
                                        }
                                    }
                                    if (v.credentialType === 'RefreshToken') rt = v;
                                } catch {}
                            }
                            if (!bestAt || !bestAt.secret) return null;
                            return {
                                access_token: bestAt.secret,
                                refresh_token: rt ? rt.secret : '',
                                expires_at: bestExp,
                                scopes: bestAt.target ? bestAt.target.split(' ') : [],
                            };
                        }"""
                    )

                context.close()

                if not result or not result.get("access_token"):
                    logger.error("No valid MSAL token found in localStorage")
                    self._state = DeviceFlowState.FAILED
                    self._user_code = None
                    return None

                # Convert expires_at to expires_in
                now = int(time.time())
                expires_at = result.get("expires_at", now + 3600)
                expires_in = max(expires_at - now, 0)

                return {
                    "access_token": result["access_token"],
                    "refresh_token": result.get("refresh_token", ""),
                    "expires_in": expires_in,
                }

        except Exception as e:
            logger.error("Browser token extraction failed: %s", e)
            self._state = DeviceFlowState.FAILED
            self._user_code = None
            return None

    def _save_tokens(self, result: dict) -> None:
        """Save tokens using atomic write (same pattern as outlook_token.py)."""
        access_token = result["access_token"]
        refresh_token = result.get("refresh_token", "")
        expires_in = result.get("expires_in", 3600)

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
            "_refreshToken": refresh_token,
            "timestamp": int(time.time() * 1000),
            "source": "head-nurse-browser-extract",
        }

        path = Path(self._token_file)
        content = json.dumps(new_storage, indent=2)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            os.write(tmp_fd, content.encode())
            os.fsync(tmp_fd)
            os.close(tmp_fd)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def reset(self) -> None:
        """Reset to idle state, allowing a new flow on next failure."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._state = DeviceFlowState.IDLE
        self._user_code = None
        self._task = None
