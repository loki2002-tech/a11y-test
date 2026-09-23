"""
Playwright Browser Controller.

Wraps the Playwright async API to provide a controlled, observable browser
interface for the accessibility testing engine.  All browser interactions
MUST go through this class to ensure:

1. Consistent timeouts and waiting strategies
2. Full interaction logging for the execution trace
3. Safe, isolated browser contexts per scan run
4. Evidence capture at each interaction step
5. No secrets exposed in logs

Architecture note:
    This class is intentionally NOT an AI tool itself.  Higher-level
    AccessibilityTools (in agent/tools/) wrap these methods and expose them
    to the AI agent.  This keeps the browser layer deterministic and testable.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    ConsoleMessage,
    Page,
    Playwright,
    async_playwright,
)

from accessibility_agent.config import BrowserType, settings
from accessibility_agent.logging_config import get_logger

log = get_logger(__name__)


class BrowserController:
    """
    Manages the Playwright browser lifecycle and exposes high-level
    accessibility-testing-oriented methods.

    Usage (as async context manager):

        async with BrowserController() as browser:
            await browser.navigate("https://example.com")
            snapshot = await browser.get_accessibility_snapshot()
    """

    def __init__(
        self,
        browser_type: BrowserType | None = None,
        headless: bool | None = None,
        viewport_width: int | None = None,
        viewport_height: int | None = None,
        extra_http_headers: dict[str, str] | None = None,
        proxy_server: str | None = None,
    ) -> None:
        self._browser_type = browser_type or settings.browser_type
        self._headless = headless if headless is not None else settings.headless
        self._viewport = {
            "width": viewport_width or settings.viewport_width,
            "height": viewport_height or settings.viewport_height,
        }
        self._extra_http_headers = extra_http_headers or settings.extra_http_headers
        self._proxy = {"server": proxy_server} if proxy_server else (
            {"server": settings.proxy_server} if settings.proxy_server else None
        )

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

        self._console_messages: list[dict[str, str]] = []
        self._interaction_log: list[dict[str, Any]] = []

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch the browser.  Call once before running tests."""
        log.info("browser.starting", type=self._browser_type.value, headless=self._headless)
        self._playwright = await async_playwright().start()

        launcher = {
            BrowserType.CHROMIUM: self._playwright.chromium,
            BrowserType.FIREFOX: self._playwright.firefox,
            BrowserType.WEBKIT: self._playwright.webkit,
        }[self._browser_type]

        launch_kwargs: dict[str, Any] = {"headless": self._headless}
        if settings.slow_mo:
            launch_kwargs["slow_mo"] = settings.slow_mo

        self._browser = await launcher.launch(**launch_kwargs)

        context_kwargs: dict[str, Any] = {
            "viewport": self._viewport,
            "extra_http_headers": self._redact_headers(self._extra_http_headers),
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        if self._proxy:
            context_kwargs["proxy"] = self._proxy

        self._context = await self._browser.new_context(**context_kwargs)
        self._context.set_default_timeout(settings.browser_timeout)
        self._context.set_default_navigation_timeout(settings.navigation_timeout)

        self._page = await self._context.new_page()
        self._page.on("console", self._on_console)

        log.info("browser.started", viewport=self._viewport)

    async def stop(self) -> None:
        """Gracefully close the browser."""
        log.info("browser.stopping")
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        log.info("browser.stopped")

    async def __aenter__(self) -> "BrowserController":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    # ── Authentication ────────────────────────────────────────────────────

    async def load_auth_state(self, state_path: Path) -> None:
        """
        Restore a previously saved Playwright browser storage state.

        This injects cookies, localStorage, and sessionStorage from a JSON
        file created by Playwright's ``context.storage_state()`` method.

        Use this for OAuth/SSO-protected apps where programmatic login is
        not feasible.  Generate the state file once with::

            playwright codegen --save-storage=auth.json https://your-app.com

        Then pass ``--auth-state auth.json`` to the CLI.

        Args:
            state_path: Path to the Playwright storage state JSON file.

        Raises:
            FileNotFoundError: If the state file does not exist.
            RuntimeError: If the browser context is not yet started.
        """
        if not self._context:
            raise RuntimeError("BrowserController.start() must be called before load_auth_state().")

        if not state_path.exists():
            raise FileNotFoundError(
                f"Auth state file not found: {state_path}\n"
                "Generate it with: playwright codegen --save-storage=auth.json <url>"
            )

        import json as _json
        state_data = _json.loads(state_path.read_text(encoding="utf-8"))

        # Inject cookies
        cookies = state_data.get("cookies", [])
        if cookies:
            await self._context.add_cookies(cookies)
            log.info("browser.auth.cookies_loaded", count=len(cookies))

        # Inject localStorage / sessionStorage via JS evaluation on a blank page
        origins = state_data.get("origins", [])
        if origins and self._page:
            for origin in origins:
                origin_url = origin.get("origin", "")
                local_storage = origin.get("localStorage", [])
                if local_storage and origin_url:
                    try:
                        await self._page.goto(origin_url, wait_until="domcontentloaded", timeout=10_000)
                        for item in local_storage:
                            await self._page.evaluate(
                                "([k, v]) => localStorage.setItem(k, v)",
                                [item["name"], item["value"]],
                            )
                        log.info(
                            "browser.auth.localstorage_loaded",
                            origin=origin_url,
                            items=len(local_storage),
                        )
                    except Exception as exc:
                        log.warning(
                            "browser.auth.localstorage_failed",
                            origin=origin_url,
                            error=str(exc),
                        )

        log.info("browser.auth.state_loaded", path=str(state_path))

    async def perform_login(
        self,
        login_url: str,
        username: str,
        password: str,
        username_selector: str = "input[type='email'], input[type='text'], input[name='username'], input[name='email']",
        password_selector: str = "input[type='password']",
        submit_selector: str = "button[type='submit'], input[type='submit']",
        success_url_contains: str | None = None,
    ) -> bool:
        """
        Automatically fill and submit a login form.

        This navigates to *login_url*, fills the username and password fields
        using :meth:`type_text`, submits the form, and waits for a redirect
        that indicates successful authentication.

        Args:
            login_url: The URL of the login page.
            username: The login username or email address.
            password: The login password.  **Never logged or included in evidence.**
            username_selector: CSS selector(s) for the username field (comma-separated fallbacks).
            password_selector: CSS selector for the password field.
            submit_selector: CSS selector for the submit button (comma-separated fallbacks).
            success_url_contains: Optional substring that the post-login URL must contain.
                                   If None, any URL change from the login page is accepted.

        Returns:
            True if login appeared successful, False otherwise.
        """
        if not self._page:
            raise RuntimeError("BrowserController.start() must be called before perform_login().")

        log.info("browser.auth.login_start", login_url=login_url)

        try:
            await self._page.goto(login_url, wait_until="domcontentloaded", timeout=settings.navigation_timeout)

            # Resolve the username field — try each comma-separated selector
            username_field = None
            for sel in [s.strip() for s in username_selector.split(",")]:
                try:
                    await self._page.wait_for_selector(sel, timeout=3000)
                    username_field = sel
                    break
                except Exception:
                    continue

            if not username_field:
                log.error("browser.auth.username_field_not_found", tried=username_selector)
                return False

            # Fill credentials — password is never logged
            await self._page.fill(username_field, username)
            await self._page.fill(password_selector, password)
            log.debug("browser.auth.credentials_filled")

            # Click the submit button
            submit_field = None
            for sel in [s.strip() for s in submit_selector.split(",")]:
                try:
                    await self._page.wait_for_selector(sel, timeout=2000)
                    submit_field = sel
                    break
                except Exception:
                    continue

            if not submit_field:
                # Try pressing Enter as fallback
                await self._page.keyboard.press("Enter")
            else:
                await self._page.click(submit_field)

            # Wait for navigation after submit
            try:
                await self._page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                await asyncio.sleep(2)

            final_url = self._page.url
            login_succeeded = final_url != login_url

            if success_url_contains:
                login_succeeded = success_url_contains in final_url

            if login_succeeded:
                log.info("browser.auth.login_success", final_url=final_url)
            else:
                log.warning("browser.auth.login_failed", final_url=final_url)

            return login_succeeded

        except Exception as exc:
            log.error("browser.auth.login_error", error=str(exc))
            return False

    async def save_auth_state(self, output_path: Path) -> None:
        """
        Save the current browser storage state (cookies + localStorage) to a JSON file.

        This allows you to reuse the authenticated session on subsequent scans
        without logging in again.

        Args:
            output_path: Where to save the storage state JSON.
        """
        if not self._context:
            raise RuntimeError("BrowserController.start() must be called before save_auth_state().")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        await self._context.storage_state(path=str(output_path))
        log.info("browser.auth.state_saved", path=str(output_path))



    async def navigate(self, url: str) -> dict[str, Any]:
        """
        Navigate to *url* and wait for the page to stabilize.

        Returns a dict with navigation metadata for the execution trace.
        """
        self._ensure_page()
        log.info("browser.navigate", url=url)
        self._log_interaction("navigate", {"url": url})

        response = await self._page.goto(  # type: ignore[union-attr]
            url,
            wait_until="domcontentloaded",
            timeout=settings.navigation_timeout,
        )

        # Additional stabilization wait for SPAs
        if settings.page_stabilization_ms > 0:
            await asyncio.sleep(settings.page_stabilization_ms / 1000)

        title = await self._page.title()  # type: ignore[union-attr]
        final_url = self._page.url  # type: ignore[union-attr]

        result = {
            "url": final_url,
            "original_url": url,
            "title": title,
            "status": response.status if response else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "viewport": f"{self._viewport['width']}x{self._viewport['height']}",
            "browser": self._browser_type.value,
        }
        log.info("browser.navigated", title=title, status=result["status"])
        return result

    # ── DOM & Source ───────────────────────────────────────────────────────

    async def get_page_source(self) -> str:
        """Return the current full page HTML source."""
        self._ensure_page()
        return await self._page.content()  # type: ignore[union-attr]

    async def get_element_outer_html(self, selector: str) -> str:
        """Return the outer HTML for the first matching element."""
        self._ensure_page()
        try:
            element = await self._page.query_selector(selector)  # type: ignore[union-attr]
            if element:
                return await element.evaluate("el => el.outerHTML")
            return ""
        except Exception as exc:
            log.warning("browser.get_outer_html.failed", selector=selector, error=str(exc))
            return ""

    async def evaluate(self, script: str) -> Any:
        """
        Evaluate JavaScript in the page context.  Use sparingly — prefer
        purpose-built methods.  All calls are logged.
        """
        self._ensure_page()
        self._log_interaction("evaluate", {"script_length": len(script)})
        return await self._page.evaluate(script)  # type: ignore[union-attr]

    # ── Accessibility ─────────────────────────────────────────────────────

    async def get_accessibility_snapshot(self, root: str | None = None) -> dict[str, Any]:
        """
        Return the browser's computed accessibility tree as a dictionary.

        NOTE: This is the browser-level accessibility tree, NOT equivalent
        to actual screen reader output.  Findings derived from this snapshot
        must be labelled as 'accessibility_tree' evidence, not 'screen_reader'.
        """
        self._ensure_page()
        log.debug("browser.ax_snapshot", root=root)
        try:
            snapshot = await self._page.locator(root or "body").aria_snapshot()  # type: ignore[union-attr]
            return {"aria_snapshot": snapshot}
        except Exception as exc:
            log.warning("browser.ax_snapshot.failed", error=str(exc))
            return {"error": str(exc)}

    async def get_computed_style(self, selector: str) -> dict[str, str]:
        """Return computed CSS properties for the first matching element."""
        self._ensure_page()
        try:
            return await self._page.evaluate(  # type: ignore[union-attr]
                """(selector) => {
                    const el = document.querySelector(selector);
                    if (!el) return {};
                    const style = window.getComputedStyle(el);
                    const result = {};
                    for (let i = 0; i < style.length; i++) {
                        const prop = style[i];
                        result[prop] = style.getPropertyValue(prop);
                    }
                    return result;
                }""",
                selector,
            )
        except Exception as exc:
            log.warning("browser.computed_style.failed", selector=selector, error=str(exc))
            return {}

    async def get_bounding_box(self, selector: str) -> dict[str, float] | None:
        """Return the bounding box for the first matching element."""
        self._ensure_page()
        try:
            element = await self._page.query_selector(selector)  # type: ignore[union-attr]
            if element:
                box = await element.bounding_box()
                return dict(box) if box else None
            return None
        except Exception as exc:
            log.warning("browser.bounding_box.failed", selector=selector, error=str(exc))
            return None

    async def get_focused_element_info(self) -> dict[str, Any]:
        """Return accessible information about the currently focused element."""
        self._ensure_page()
        return await self._page.evaluate(  # type: ignore[union-attr]
            """() => {
                const el = document.activeElement;
                if (!el || el === document.body) return { focused: false };
                return {
                    focused: true,
                    tagName: el.tagName.toLowerCase(),
                    id: el.id || null,
                    className: el.className || null,
                    role: el.getAttribute('role') || null,
                    ariaLabel: el.getAttribute('aria-label') || null,
                    ariaLabelledby: el.getAttribute('aria-labelledby') || null,
                    ariaDescribedby: el.getAttribute('aria-describedby') || null,
                    ariaHidden: el.getAttribute('aria-hidden') || null,
                    tabIndex: el.tabIndex,
                    textContent: el.textContent?.trim().slice(0, 200) || null,
                    outerHTML: el.outerHTML.slice(0, 1024),
                    selector: (function getSelector(e) {
                        if (e.id) return '#' + e.id;
                        const cls = e.getAttribute('class');
                        if (cls) return e.tagName.toLowerCase() + '.' + cls.split(' ')[0];
                        return e.tagName.toLowerCase();
                    })(el),
                };
            }"""
        )

    # ── Keyboard Interaction ───────────────────────────────────────────────

    async def press_key(self, key: str) -> dict[str, Any]:
        """
        Press a keyboard key and return the focused element after the action.
        This is the primary tool for keyboard accessibility testing.
        """
        self._ensure_page()
        log.debug("browser.key_press", key=key)
        self._log_interaction("press_key", {"key": key})

        await self._page.keyboard.press(key)  # type: ignore[union-attr]
        await asyncio.sleep(0.1)  # Brief wait for focus to settle

        focused = await self.get_focused_element_info()
        return {"key_pressed": key, "focused_element": focused}

    async def click_element(self, selector: str) -> dict[str, Any]:
        """Click an element and return post-click state."""
        self._ensure_page()
        log.debug("browser.click", selector=selector)
        self._log_interaction("click_element", {"selector": selector})

        try:
            # 5-second timeout so hallucinated selectors fail fast
            await self._page.click(selector, timeout=5000)  # type: ignore[union-attr]
            
            # Wait for network idle or animations to settle
            # We catch exceptions because if it's an SPA click, it might not trigger a load state
            try:
                await self._page.wait_for_load_state("networkidle", timeout=2000)  # type: ignore[union-attr]
            except Exception:
                pass
            
            await asyncio.sleep(0.5) # Extra buffer for CSS animations
            
            # If the click navigated us to a completely different domain, we might want to log it
            current_url = self._page.url # type: ignore[union-attr]
            
        except Exception as exc:
            log.warning("browser.click_failed", selector=selector, error=str(exc))
            return {"clicked_selector": selector, "error": str(exc)}

        focused = await self.get_focused_element_info()
        return {"clicked_selector": selector, "focused_element": focused}

    async def type_text(self, selector: str, text: str) -> None:
        """Type text into a form field."""
        self._ensure_page()
        # Never log the actual text in case it contains sensitive data
        log.debug("browser.type_text", selector=selector, char_count=len(text))
        self._log_interaction("type_text", {"selector": selector, "char_count": len(text)})
        await self._page.fill(selector, text)  # type: ignore[union-attr]

    # ── Screenshots ────────────────────────────────────────────────────────

    async def take_screenshot(
        self,
        path: Path | None = None,
        full_page: bool = False,
        element_selector: str | None = None,
    ) -> bytes:
        """
        Capture a screenshot.  Returns raw PNG bytes.

        If *path* is provided, also saves the file.
        If *element_selector* is provided, captures only that element.
        """
        self._ensure_page()
        log.debug("browser.screenshot", full_page=full_page, selector=element_selector)
        self._log_interaction("take_screenshot", {
            "full_page": full_page,
            "element_selector": element_selector,
        })

        screenshot_kwargs: dict[str, Any] = {"full_page": full_page}
        if path:
            screenshot_kwargs["path"] = str(path)

        if element_selector:
            element = await self._page.query_selector(element_selector)  # type: ignore[union-attr]
            if element:
                element_kwargs = screenshot_kwargs.copy()
                element_kwargs.pop("full_page", None)
                return await element.screenshot(**element_kwargs)

        return await self._page.screenshot(**screenshot_kwargs)  # type: ignore[union-attr]

    # ── axe-core Injection ─────────────────────────────────────────────────

    async def inject_and_run_axe(self, axe_script_path: Path) -> dict[str, Any]:
        """
        Inject the axe-core script into the page and run it.

        Returns the raw axe result dict as produced by axe.run().
        This is a deterministic evaluation step — the AI layer must NOT
        alter the raw axe results before they are stored as evidence.

        Args:
            axe_script_path: Path to the axe.min.js file.

        Returns:
            Raw axe-core result dict with violations, passes, incomplete,
            inapplicable arrays.
        """
        self._ensure_page()
        log.info("axe.injecting", path=str(axe_script_path))

        axe_script = axe_script_path.read_text(encoding="utf-8")
        await self._page.evaluate(axe_script)  # type: ignore[union-attr]

        tags = settings.axe_tags
        run_config = {
            "runOnly": {"type": "tag", "values": tags},
            "resultTypes": ["violations", "passes", "incomplete", "inapplicable"],
        }

        log.info("axe.running", tags=tags)
        self._log_interaction("run_axe", {"tags": tags})

        result = await self._page.evaluate(  # type: ignore[union-attr]
            """(config) => {
                return new Promise((resolve, reject) => {
                    axe.run(document, config, (err, results) => {
                        if (err) reject(err);
                        else resolve(results);
                    });
                });
            }""",
            run_config,
        )

        log.info(
            "axe.complete",
            violations=len(result.get("violations", [])),
            passes=len(result.get("passes", [])),
            incomplete=len(result.get("incomplete", [])),
            inapplicable=len(result.get("inapplicable", [])),
        )
        return result

    # ── Console Logs ───────────────────────────────────────────────────────

    def _on_console(self, msg: ConsoleMessage) -> None:
        """Collect console messages during the session."""
        self._console_messages.append({
            "type": msg.type,
            "text": msg.text,
            "location": f"{msg.location.get('url', '')}:{msg.location.get('lineNumber', '')}",
        })

    def get_console_messages(self) -> list[dict[str, str]]:
        """Return collected console messages."""
        return list(self._console_messages)

    # ── Interaction Trace ──────────────────────────────────────────────────

    def get_interaction_log(self) -> list[dict[str, Any]]:
        """Return the full interaction log for inclusion in the execution trace."""
        return list(self._interaction_log)

    def _log_interaction(self, action: str, args: dict[str, Any]) -> None:
        self._interaction_log.append({
            "action": action,
            "args": args,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "url": self._page.url if self._page else "",
        })

    # ── Helpers ────────────────────────────────────────────────────────────

    def _ensure_page(self) -> None:
        if not self._page:
            raise RuntimeError(
                "BrowserController.start() must be called before browser operations."
            )

    @staticmethod
    def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
        """Strip sensitive HTTP headers before logging or storing."""
        redacted = {
            k: "***REDACTED***" if k.lower() in settings.redact_headers else v
            for k, v in headers.items()
        }
        return redacted

    @property
    def current_url(self) -> str:
        return self._page.url if self._page else ""

    @property
    def browser_type_name(self) -> str:
        return self._browser_type.value

    @property
    def viewport_string(self) -> str:
        return f"{self._viewport['width']}x{self._viewport['height']}"
