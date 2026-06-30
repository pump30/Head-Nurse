"""Tests for device_code_flow.py module (Authorization Code Flow)."""

import asyncio
import json
import time
from http.client import HTTPConnection
from threading import Thread
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from kanban_agent.device_code_flow import (
    DeviceCodeFlowManager,
    DeviceFlowState,
    REDIRECT_PORT,
)


@pytest.fixture
def token_file(tmp_path):
    """Create a temporary token file path."""
    f = tmp_path / "sap_tokens.json"
    f.write_text(json.dumps({"tokens": [], "_refreshToken": "old"}))
    return str(f)


@pytest.fixture
def manager(token_file):
    return DeviceCodeFlowManager(token_file)


def _make_request():
    """Create a dummy request for httpx.Response."""
    return httpx.Request("POST", "https://login.microsoftonline.com/test/oauth2/v2.0/token")


def _mock_token_success():
    """Mock a successful token exchange response."""
    return httpx.Response(
        200,
        json={
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600,
            "token_type": "Bearer",
        },
        request=_make_request(),
    )


def _mock_token_error():
    """Mock a failed token exchange."""
    return httpx.Response(
        400,
        json={"error": "invalid_grant", "error_description": "bad code"},
        request=_make_request(),
    )


class TestDeviceCodeFlowManager:
    def test_initial_state(self, manager):
        assert manager.state == DeviceFlowState.IDLE
        assert not manager.is_in_progress
        assert manager.user_code is None

    def test_get_auth_url(self, manager):
        url = manager.get_auth_url()
        assert "login.microsoftonline.com" in url
        assert "response_type=code" in url
        assert f"localhost%3A{REDIRECT_PORT}" in url or f"localhost:{REDIRECT_PORT}" in url

    @pytest.mark.asyncio
    async def test_initiate_starts_flow(self, manager):
        """initiate() should transition state and return True."""
        # Patch the _wait_for_auth_code to immediately return None (timeout)
        with patch.object(manager, "_wait_for_auth_code", new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = None

            result = await manager.initiate()
            assert result is True

            if manager._task:
                await manager._task

            # Flow ended because _wait_for_auth_code returned None
            assert manager.state in (DeviceFlowState.FAILED, DeviceFlowState.EXPIRED)

    @pytest.mark.asyncio
    async def test_no_double_initiate(self, manager):
        """Second initiate() returns False while flow is in progress."""
        # Make _wait_for_auth_code block forever
        never_done = asyncio.Future()

        async def block_forever():
            await never_done
            return None

        with patch.object(manager, "_wait_for_auth_code", side_effect=block_forever):
            result1 = await manager.initiate()
            assert result1 is True
            await asyncio.sleep(0.05)

            result2 = await manager.initiate()
            assert result2 is False

            # Cleanup
            manager.reset()

    @pytest.mark.asyncio
    async def test_full_success_flow(self, manager, token_file):
        """Full flow: get code from callback → exchange → save tokens."""

        async def fake_wait_for_code():
            return "fake_auth_code_123"

        with patch.object(manager, "_wait_for_auth_code", side_effect=fake_wait_for_code):
            with patch("kanban_agent.device_code_flow.httpx.AsyncClient") as mock_cls:
                mock_instance = AsyncMock()
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_instance.post = AsyncMock(return_value=_mock_token_success())

                await manager.initiate()
                if manager._task:
                    await manager._task

                assert manager.state == DeviceFlowState.SUCCESS

                # Verify tokens saved
                data = json.loads(open(token_file).read())
                assert data["tokens"][0]["token"] == "new_access_token"
                assert data["_refreshToken"] == "new_refresh_token"
                assert data["source"] == "head-nurse-auth-code-flow"

    @pytest.mark.asyncio
    async def test_exchange_failure(self, manager):
        """Token exchange error → FAILED state."""

        async def fake_wait_for_code():
            return "fake_auth_code_123"

        with patch.object(manager, "_wait_for_auth_code", side_effect=fake_wait_for_code):
            with patch("kanban_agent.device_code_flow.httpx.AsyncClient") as mock_cls:
                mock_instance = AsyncMock()
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_instance.post = AsyncMock(
                    side_effect=httpx.ConnectError("network down")
                )

                await manager.initiate()
                if manager._task:
                    await manager._task

                assert manager.state == DeviceFlowState.FAILED

    @pytest.mark.asyncio
    async def test_reset_allows_new_flow(self, manager):
        """After reset(), a new flow can be initiated."""
        with patch.object(manager, "_wait_for_auth_code", new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = None

            await manager.initiate()
            if manager._task:
                await manager._task

            manager.reset()
            assert manager.state == DeviceFlowState.IDLE
            assert not manager.is_in_progress

    @pytest.mark.asyncio
    async def test_callback_server_receives_code(self, manager):
        """The local HTTP server correctly receives auth code from browser redirect."""
        # Start the flow but we'll manually hit the callback
        code_received = asyncio.Future()

        original_exchange = manager._exchange_code

        async def capture_exchange(code):
            code_received.set_result(code)
            # Return a mock success
            return {
                "access_token": "test_token",
                "refresh_token": "test_refresh",
                "expires_in": 3600,
            }

        manager._exchange_code = capture_exchange

        await manager.initiate()
        await asyncio.sleep(0.2)  # let server start

        # Simulate browser redirect by hitting the callback
        def hit_callback():
            try:
                conn = HTTPConnection("localhost", REDIRECT_PORT)
                conn.request("GET", "/callback?code=test_code_from_browser")
                conn.getresponse()
                conn.close()
            except Exception:
                pass

        Thread(target=hit_callback, daemon=True).start()

        if manager._task:
            await manager._task

        assert manager.state == DeviceFlowState.SUCCESS
        result = await code_received
        assert result == "test_code_from_browser"
