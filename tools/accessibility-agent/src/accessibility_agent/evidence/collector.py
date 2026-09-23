"""
Evidence Collector.

Responsible for capturing and persisting all forms of evidence:
- Screenshots (full page and element-level)
- DOM snapshots (full HTML and element outer HTML)
- Accessibility tree dumps
- Computed style captures
- Console and network logs

Evidence files are stored in an organized directory structure:
    evidence/{run_id}/{timestamp}_{type}_{hash}.{ext}

All evidence is returned as EvidenceItem objects for inclusion in findings.
Evidence files are NEVER sent to the LLM — only references and key excerpts
are included in prompts to manage token usage.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from accessibility_agent.accessibility.browser import BrowserController
from accessibility_agent.config import settings
from accessibility_agent.logging_config import get_logger
from accessibility_agent.wcag.schemas import EvidenceItem

log = get_logger(__name__)


class EvidenceCollector:
    """
    Captures and persists accessibility testing evidence.

    Produces EvidenceItem objects that can be attached to Finding objects.
    All files are saved to disk under the configured evidence_dir.

    Screenshot Deduplication:
        Instead of embedding one full-page PNG per finding (which causes 73MB+
        JSON files), the collector maintains a shared cache of screenshots keyed
        by a content hash. Findings store a lightweight `screenshot_ref_id`
        pointing to the shared map on ScanResult.
    """

    def __init__(self, browser: BrowserController, run_id: str) -> None:
        self._browser = browser
        self._run_id = run_id
        self._evidence_dir = settings.evidence_dir / run_id
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        # Shared screenshot cache: content_hash → base64
        self._screenshot_cache: dict[str, str] = {}
        log.info("evidence_collector.initialized", dir=str(self._evidence_dir))

    @property
    def shared_screenshots(self) -> dict[str, str]:
        """Return the shared screenshot cache for storing in ScanResult."""
        return dict(self._screenshot_cache)

    # ── Screenshots ────────────────────────────────────────────────────────

    async def capture_screenshot(
        self,
        label: str = "page",
        element_selector: str | None = None,
        full_page: bool = False,
    ) -> EvidenceItem:
        """
        Capture a screenshot and save it to disk.

        For full-page screenshots, uses content-based deduplication:
        if an identical screenshot was already captured in this run,
        returns a lightweight reference item instead of a duplicate embed.

        Returns an EvidenceItem with the file path and base64-encoded
        data for embedding in reports.
        """
        if not settings.screenshots_enabled:
            return self._empty_evidence("screenshot", "Screenshots disabled in config")

        timestamp = datetime.now(timezone.utc)
        filename = self._make_filename(label, "screenshot", "png", timestamp)
        file_path = self._evidence_dir / filename

        png_bytes = await self._browser.take_screenshot(
            path=file_path,
            full_page=full_page,
            element_selector=element_selector,
        )

        b64 = base64.b64encode(png_bytes).decode("ascii")

        # For full-page screenshots, deduplicate by content hash
        if full_page and not element_selector:
            content_hash = hashlib.sha256(png_bytes).hexdigest()[:16]
            ref_id = f"SS-{content_hash.upper()}"

            if ref_id not in self._screenshot_cache:
                self._screenshot_cache[ref_id] = b64
                log.debug("evidence.screenshot_cached", ref_id=ref_id, file=str(file_path))
            else:
                log.debug("evidence.screenshot_deduplicated", ref_id=ref_id)

            return EvidenceItem(
                evidence_type="screenshot",
                description=f"Screenshot: {label}",
                data="",  # Empty — use screenshot_ref_id to look up the shared copy
                screenshot_ref_id=ref_id,
                file_path=str(file_path.relative_to(settings.evidence_dir)),
                url=self._browser.current_url,
                timestamp=timestamp,
                browser=self._browser.browser_type_name,
                viewport=self._browser.viewport_string,
            )

        log.debug("evidence.screenshot_captured", file=str(file_path))
        return EvidenceItem(
            evidence_type="screenshot",
            description=f"Screenshot: {label}" + (f" (element: {element_selector})" if element_selector else ""),
            data=b64,
            file_path=str(file_path.relative_to(settings.evidence_dir)),
            url=self._browser.current_url,
            timestamp=timestamp,
            browser=self._browser.browser_type_name,
            viewport=self._browser.viewport_string,
        )

    # ── DOM Snapshot ────────────────────────────────────────────────────────

    async def capture_dom_snapshot(self, label: str = "page") -> EvidenceItem:
        """Capture the full page HTML source."""
        if not settings.dom_snapshots_enabled:
            return self._empty_evidence("dom_snippet", "DOM snapshots disabled in config")

        timestamp = datetime.now(timezone.utc)
        html = await self._browser.get_page_source()

        filename = self._make_filename(label, "dom", "html", timestamp)
        file_path = self._evidence_dir / filename
        file_path.write_text(html, encoding="utf-8")

        log.debug("evidence.dom_snapshot_captured", size=len(html), file=str(file_path))
        return EvidenceItem(
            evidence_type="dom_snippet",
            description=f"Full DOM snapshot: {label}",
            data=html[:4096],  # Store excerpt in the item; full file is on disk
            file_path=str(file_path.relative_to(settings.evidence_dir)),
            url=self._browser.current_url,
            timestamp=timestamp,
            browser=self._browser.browser_type_name,
            viewport=self._browser.viewport_string,
        )

    async def capture_element_html(
        self, selector: str, label: str = "element"
    ) -> EvidenceItem:
        """Capture the outer HTML of a specific element."""
        timestamp = datetime.now(timezone.utc)
        html = await self._browser.get_element_outer_html(selector)

        return EvidenceItem(
            evidence_type="dom_snippet",
            description=f"Element HTML: {label} ({selector})",
            data=html,
            url=self._browser.current_url,
            timestamp=timestamp,
            browser=self._browser.browser_type_name,
            viewport=self._browser.viewport_string,
        )

    # ── Accessibility Tree ─────────────────────────────────────────────────

    async def capture_ax_tree(
        self, label: str = "page", root_selector: str | None = None
    ) -> EvidenceItem:
        """
        Capture the browser accessibility tree.

        IMPORTANT: The captured tree represents the browser's internal
        accessibility model, NOT actual screen reader output.
        Evidence type is 'ax_tree', NOT 'screen_reader'.
        """
        if not settings.ax_tree_snapshots_enabled:
            return self._empty_evidence("ax_tree", "AX tree snapshots disabled in config")

        timestamp = datetime.now(timezone.utc)
        snapshot = await self._browser.get_accessibility_snapshot(root=root_selector)

        snapshot_json = json.dumps(snapshot, indent=2, default=str)

        filename = self._make_filename(label, "ax_tree", "json", timestamp)
        file_path = self._evidence_dir / filename
        file_path.write_text(snapshot_json, encoding="utf-8")

        log.debug("evidence.ax_tree_captured", file=str(file_path))
        return EvidenceItem(
            evidence_type="ax_tree",
            description=(
                f"Browser accessibility tree snapshot: {label}. "
                "NOTE: This is the browser AX tree, NOT screen reader output."
            ),
            data=snapshot_json[:4096],
            file_path=str(file_path.relative_to(settings.evidence_dir)),
            url=self._browser.current_url,
            timestamp=timestamp,
            browser=self._browser.browser_type_name,
            viewport=self._browser.viewport_string,
        )

    # ── Computed Style ─────────────────────────────────────────────────────

    async def capture_computed_style(self, selector: str) -> EvidenceItem:
        """
        Capture computed CSS for an element.  Useful for focus visibility
        and contrast evidence.
        """
        timestamp = datetime.now(timezone.utc)
        style = await self._browser.get_computed_style(selector)

        # Filter to accessibility-relevant properties only to reduce noise
        relevant_keys = {
            "outline", "outline-width", "outline-style", "outline-color",
            "outline-offset", "border", "border-color", "border-width",
            "background-color", "color", "opacity", "visibility", "display",
            "font-size", "font-weight", "letter-spacing", "line-height",
            "word-spacing", "box-shadow", "z-index", "position",
        }
        filtered = {k: v for k, v in style.items() if k in relevant_keys}

        return EvidenceItem(
            evidence_type="computed_style",
            description=f"Computed CSS style for: {selector}",
            data=json.dumps(filtered, indent=2),
            url=self._browser.current_url,
            timestamp=timestamp,
            browser=self._browser.browser_type_name,
            viewport=self._browser.viewport_string,
        )

    # ── Console Log ────────────────────────────────────────────────────────

    def capture_console_log(self) -> EvidenceItem:
        """Return console messages collected during the current session."""
        messages = self._browser.get_console_messages()
        return EvidenceItem(
            evidence_type="console_log",
            description="Browser console messages during the test session",
            data=json.dumps(messages, indent=2),
            url=self._browser.current_url,
            timestamp=datetime.now(timezone.utc),
            browser=self._browser.browser_type_name,
            viewport=self._browser.viewport_string,
        )

    # ── Focus State ────────────────────────────────────────────────────────

    async def capture_focused_element(self, label: str = "focus_state") -> EvidenceItem:
        """
        Capture the current focus state including:
        - Focused element info (from JS)
        - Screenshot (to visually verify focus indicator)
        - Computed outline/border styles
        """
        timestamp = datetime.now(timezone.utc)
        focused = await self._browser.get_focused_element_info()

        data: dict[str, Any] = {"focused_element": focused}

        # Capture computed style if we have a selector
        if focused.get("focused") and focused.get("selector"):
            style = await self._browser.get_computed_style(focused["selector"])
            relevant = {
                k: v for k, v in style.items()
                if k in {"outline", "outline-width", "outline-style", "outline-color",
                         "outline-offset", "box-shadow", "border"}
            }
            data["focus_styles"] = relevant

        return EvidenceItem(
            evidence_type="interaction_log",
            description=f"Focus state capture: {label}",
            data=json.dumps(data, indent=2, default=str),
            url=self._browser.current_url,
            timestamp=timestamp,
            browser=self._browser.browser_type_name,
            viewport=self._browser.viewport_string,
        )

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _make_filename(label: str, type_: str, ext: str, timestamp: datetime) -> str:
        ts = timestamp.strftime("%Y%m%d_%H%M%S_%f")
        safe_label = re.sub(r"[^\w\-]", "_", label)[:40]
        return f"{ts}_{type_}_{safe_label}.{ext}"

    @staticmethod
    def _empty_evidence(type_: str, reason: str) -> EvidenceItem:
        return EvidenceItem(
            evidence_type=type_,
            description=reason,
            data="",
            timestamp=datetime.now(timezone.utc),
        )


import re  # noqa: E402 (must be after class for _make_filename reference)
