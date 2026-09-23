"""
Unit tests for Phase Auth: Authentication support in BrowserController and ScanOrchestrator.

All tests are fully mocked — no real browser launched, no network calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 1 — BrowserController.load_auth_state()
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadAuthState:

    def _make_browser(self):
        """Create a BrowserController with mocked internals."""
        from accessibility_agent.accessibility.browser import BrowserController
        ctrl = BrowserController.__new__(BrowserController)
        ctrl._context = AsyncMock()
        ctrl._page = AsyncMock()
        ctrl._page.url = "https://example.com"
        ctrl._interaction_log = []
        ctrl._console_messages = []
        return ctrl

    @pytest.mark.asyncio
    async def test_load_auth_state_raises_if_no_context(self, tmp_path):
        from accessibility_agent.accessibility.browser import BrowserController
        ctrl = BrowserController.__new__(BrowserController)
        ctrl._context = None
        ctrl._page = None

        state_file = tmp_path / "auth.json"
        state_file.write_text('{"cookies": [], "origins": []}')

        with pytest.raises(RuntimeError, match="start\\(\\) must be called"):
            await ctrl.load_auth_state(state_file)

    @pytest.mark.asyncio
    async def test_load_auth_state_raises_if_file_missing(self, tmp_path):
        ctrl = self._make_browser()
        missing_file = tmp_path / "does_not_exist.json"

        with pytest.raises(FileNotFoundError, match="Auth state file not found"):
            await ctrl.load_auth_state(missing_file)

    @pytest.mark.asyncio
    async def test_load_auth_state_injects_cookies(self, tmp_path):
        ctrl = self._make_browser()
        cookies = [{"name": "session", "value": "abc123", "domain": "example.com", "path": "/"}]
        state_file = tmp_path / "auth.json"
        state_file.write_text(json.dumps({"cookies": cookies, "origins": []}))

        await ctrl.load_auth_state(state_file)

        ctrl._context.add_cookies.assert_awaited_once_with(cookies)

    @pytest.mark.asyncio
    async def test_load_auth_state_skips_cookies_if_empty(self, tmp_path):
        ctrl = self._make_browser()
        state_file = tmp_path / "auth.json"
        state_file.write_text(json.dumps({"cookies": [], "origins": []}))

        await ctrl.load_auth_state(state_file)

        ctrl._context.add_cookies.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_load_auth_state_injects_localstorage(self, tmp_path):
        ctrl = self._make_browser()
        ctrl._page.goto = AsyncMock()
        ctrl._page.evaluate = AsyncMock()

        origins = [{
            "origin": "https://example.com",
            "localStorage": [{"name": "token", "value": "xyz"}],
        }]
        state_file = tmp_path / "auth.json"
        state_file.write_text(json.dumps({"cookies": [], "origins": origins}))

        await ctrl.load_auth_state(state_file)

        ctrl._page.goto.assert_awaited_once()
        ctrl._page.evaluate.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 2 — BrowserController.perform_login()
# ─────────────────────────────────────────────────────────────────────────────

class TestPerformLogin:

    def _make_browser(self, final_url: str = "https://example.com/dashboard"):
        from accessibility_agent.accessibility.browser import BrowserController
        ctrl = BrowserController.__new__(BrowserController)
        ctrl._context = AsyncMock()
        ctrl._page = AsyncMock()
        ctrl._page.url = final_url
        ctrl._page.goto = AsyncMock()
        ctrl._page.fill = AsyncMock()
        ctrl._page.click = AsyncMock()
        ctrl._page.wait_for_selector = AsyncMock()
        ctrl._page.wait_for_load_state = AsyncMock()
        ctrl._page.keyboard = AsyncMock()
        ctrl._interaction_log = []
        ctrl._console_messages = []
        return ctrl

    @pytest.mark.asyncio
    async def test_perform_login_raises_if_no_page(self):
        from accessibility_agent.accessibility.browser import BrowserController
        ctrl = BrowserController.__new__(BrowserController)
        ctrl._page = None

        with pytest.raises(RuntimeError, match="start\\(\\) must be called"):
            await ctrl.perform_login("https://example.com/login", "user", "pass")

    @pytest.mark.asyncio
    async def test_perform_login_returns_true_on_url_change(self):
        ctrl = self._make_browser(final_url="https://example.com/dashboard")
        ctrl._page.url = "https://example.com/dashboard"

        result = await ctrl.perform_login(
            login_url="https://example.com/login",
            username="admin",
            password="secret",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_perform_login_returns_false_if_url_unchanged(self):
        ctrl = self._make_browser(final_url="https://example.com/login")
        ctrl._page.url = "https://example.com/login"

        result = await ctrl.perform_login(
            login_url="https://example.com/login",
            username="bad",
            password="wrong",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_perform_login_checks_success_url_contains(self):
        ctrl = self._make_browser(final_url="https://example.com/dashboard")
        ctrl._page.url = "https://example.com/dashboard"

        # dashboard contains "dashboard" → success
        result = await ctrl.perform_login(
            login_url="https://example.com/login",
            username="admin",
            password="secret",
            success_url_contains="dashboard",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_perform_login_fails_if_success_url_not_found(self):
        ctrl = self._make_browser(final_url="https://example.com/dashboard")
        ctrl._page.url = "https://example.com/dashboard"

        result = await ctrl.perform_login(
            login_url="https://example.com/login",
            username="admin",
            password="secret",
            success_url_contains="EXPECTED_BUT_MISSING",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_perform_login_never_logs_password(self):
        """Verify password is NOT included in any log interactions."""
        ctrl = self._make_browser()
        ctrl._page.url = "https://example.com/dashboard"

        await ctrl.perform_login(
            login_url="https://example.com/login",
            username="admin",
            password="TOP_SECRET_PASSWORD",
        )

        # Inspect interaction log — password must never appear
        logged_args = str(ctrl._interaction_log)
        assert "TOP_SECRET_PASSWORD" not in logged_args

    @pytest.mark.asyncio
    async def test_perform_login_returns_false_on_exception(self):
        ctrl = self._make_browser()
        ctrl._page.goto = AsyncMock(side_effect=Exception("Network error"))

        result = await ctrl.perform_login(
            login_url="https://example.com/login",
            username="admin",
            password="secret",
        )
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 3 — ScanOrchestrator auth integration
# ─────────────────────────────────────────────────────────────────────────────

class TestOrchestratorAuth:

    @pytest.mark.asyncio
    async def test_orchestrator_aborts_if_auth_state_file_missing(self, tmp_path):
        from accessibility_agent.agent.orchestrator import ScanOrchestrator

        missing_file = tmp_path / "auth.json"
        # Do NOT create the file

        with patch("accessibility_agent.agent.orchestrator.BrowserController") as MockBrowser:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.browser_type_name = "chromium"
            mock_ctx.viewport_string = "1280x720"
            mock_ctx.load_auth_state = AsyncMock(
                side_effect=FileNotFoundError("Auth state file not found")
            )
            MockBrowser.return_value = mock_ctx

            orchestrator = ScanOrchestrator(
                url="https://example.com",
                auth_state_path=missing_file,
            )
            result = await orchestrator.run()

        # Must have aborted with an error in the result
        assert len(result.errors) > 0
        assert any("Auth state" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_orchestrator_aborts_if_login_missing_password(self, tmp_path):
        from accessibility_agent.agent.orchestrator import ScanOrchestrator

        with patch("accessibility_agent.agent.orchestrator.BrowserController") as MockBrowser:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.browser_type_name = "chromium"
            mock_ctx.viewport_string = "1280x720"
            MockBrowser.return_value = mock_ctx

            orchestrator = ScanOrchestrator(
                url="https://example.com/dashboard",
                login_url="https://example.com/login",
                login_username="admin",
                login_password=None,  # Missing!
            )
            result = await orchestrator.run()

        assert len(result.errors) > 0
        assert any("login_password" in e or "missing" in e.lower() for e in result.errors)

    @pytest.mark.asyncio
    async def test_orchestrator_aborts_if_login_fails(self, tmp_path):
        from accessibility_agent.agent.orchestrator import ScanOrchestrator

        with patch("accessibility_agent.agent.orchestrator.BrowserController") as MockBrowser:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.browser_type_name = "chromium"
            mock_ctx.viewport_string = "1280x720"
            mock_ctx.perform_login = AsyncMock(return_value=False)  # Login failed!
            MockBrowser.return_value = mock_ctx

            orchestrator = ScanOrchestrator(
                url="https://example.com/dashboard",
                login_url="https://example.com/login",
                login_username="admin",
                login_password="wrong_password",
            )
            result = await orchestrator.run()

        assert len(result.errors) > 0
        assert any("login" in e.lower() or "failed" in e.lower() for e in result.errors)
