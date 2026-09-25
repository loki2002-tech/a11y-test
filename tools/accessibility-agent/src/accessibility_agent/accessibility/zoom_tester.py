"""
Zoom Reflow Tester — WCAG 1.4.10 Compliance

Tests that the page renders correctly at 400% zoom (equivalent to a 320px viewport)
without requiring horizontal scrolling — a critical requirement for users with low vision
who rely on browser zoom.

WCAG 1.4.10 Reflow requires:
    - Content reflows into a single column at 320 CSS pixels wide.
    - No horizontal scrollbar appears.
    - No text clips, truncates, or overflows its container.
    - Interactive controls remain operable.
"""

from __future__ import annotations

from typing import Any

from accessibility_agent.logging_config import get_logger
from accessibility_agent.wcag.schemas import WCAGLevel, WCAGPrinciple

log = get_logger(__name__)

# 320px wide = 1280px wide page at 400% zoom (WCAG 1.4.10 reference resolution)
_ZOOM_VIEWPORT_WIDTH = 320
_ZOOM_VIEWPORT_HEIGHT = 256
_OVERFLOW_THRESHOLD_PX = 5  # allow minor sub-pixel rounding


class ZoomReflowTester:
    """
    Detects WCAG 1.4.10 Reflow violations by simulating 400% zoom.

    Usage:
        tester = ZoomReflowTester(browser)
        findings = await tester.run(url, page_title)
    """

    def __init__(self, browser: Any) -> None:
        self._browser = browser

    async def run(self, url: str, page_title: str) -> list[dict[str, Any]]:
        """
        Run the zoom reflow test at 320px wide viewport.
        Returns a list of Finding-compatible dicts.
        """
        findings: list[dict[str, Any]] = []
        original_viewport = None

        try:
            # Save original viewport
            original_viewport = await self._browser.evaluate("() => ({ w: window.innerWidth, h: window.innerHeight })")

            log.info("zoom_tester.starting", url=url, viewport=f"{_ZOOM_VIEWPORT_WIDTH}x{_ZOOM_VIEWPORT_HEIGHT}")

            # Set viewport to simulate 400% zoom
            await self._browser._page.set_viewport_size({
                "width": _ZOOM_VIEWPORT_WIDTH,
                "height": _ZOOM_VIEWPORT_HEIGHT,
            })

            # Wait for reflow
            import asyncio
            await asyncio.sleep(0.5)

            # Check for horizontal overflow (illegal scrollbar)
            scroll_data = await self._browser.evaluate("""() => ({
                scrollWidth: document.documentElement.scrollWidth,
                clientWidth: document.documentElement.clientWidth,
                bodyScrollWidth: document.body ? document.body.scrollWidth : 0,
            })""")

            scroll_width = scroll_data.get("scrollWidth", 0)
            client_width = scroll_data.get("clientWidth", _ZOOM_VIEWPORT_WIDTH)
            overflow_px = scroll_width - client_width

            if overflow_px > _OVERFLOW_THRESHOLD_PX:
                log.warning(
                    "zoom_tester.horizontal_overflow_detected",
                    overflow_px=overflow_px,
                    scroll_width=scroll_width,
                    client_width=client_width,
                )
                findings.append({
                    "rule_id": "wcag-1-4-10-reflow",
                    "wcag": {
                        "success_criterion": "1.4.10",
                        "level": WCAGLevel.AA.value,
                        "title": "Reflow",
                        "principle": WCAGPrinciple.PERCEIVABLE.value,
                        "url": "https://www.w3.org/WAI/WCAG22/Understanding/reflow.html",
                    },
                    "description": (
                        f"Page requires horizontal scrolling at 320px viewport width "
                        f"(overflow: {overflow_px}px). This fails WCAG 1.4.10 Reflow for users "
                        f"who zoom to 400% on a standard 1280px screen."
                    ),
                    "element": {"html": "<html>", "selector": "html"},
                    "impact": "serious",
                    "url": url,
                    "page_title": page_title,
                    "source": "zoom_tester",
                    "evidence": {
                        "scroll_width_px": scroll_width,
                        "client_width_px": client_width,
                        "overflow_px": overflow_px,
                        "viewport": f"{_ZOOM_VIEWPORT_WIDTH}x{_ZOOM_VIEWPORT_HEIGHT}",
                    },
                })

            # Check for clipped / overflowing text elements
            clipped = await self._browser.evaluate(f"""() => {{
                const overflowing = [];
                const elements = document.querySelectorAll('p, h1, h2, h3, h4, h5, h6, li, td, th, span, a, button, label');
                for (const el of elements) {{
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    if (style.overflow === 'hidden' && el.scrollWidth > el.clientWidth + {_OVERFLOW_THRESHOLD_PX}) {{
                        overflowing.push({{
                            tag: el.tagName.toLowerCase(),
                            text: el.textContent.trim().slice(0, 80),
                            scrollWidth: el.scrollWidth,
                            clientWidth: el.clientWidth,
                        }});
                        if (overflowing.length >= 5) break; // cap at 5
                    }}
                }}
                return overflowing;
            }}""")

            if clipped:
                for item in clipped:
                    findings.append({
                        "rule_id": "wcag-1-4-10-reflow-text-clip",
                        "wcag": {
                            "success_criterion": "1.4.10",
                            "level": WCAGLevel.AA.value,
                            "title": "Reflow",
                            "principle": WCAGPrinciple.PERCEIVABLE.value,
                            "url": "https://www.w3.org/WAI/WCAG22/Understanding/reflow.html",
                        },
                        "description": (
                            f"Text content is clipped and invisible at 400% zoom on a "
                            f"<{item['tag']}> element: \"{item['text'][:60]}...\". "
                            f"This means low-vision users who rely on zoom lose access to this content."
                        ),
                        "element": {
                            "html": f"<{item['tag']}>{item['text'][:60]}</{item['tag']}>",
                            "selector": item["tag"],
                        },
                        "impact": "serious",
                        "url": url,
                        "page_title": page_title,
                        "source": "zoom_tester",
                        "evidence": item,
                    })

            log.info("zoom_tester.complete", findings=len(findings), url=url)

        except Exception as exc:
            log.error("zoom_tester.failed", error=str(exc), url=url)
        finally:
            # Always restore original viewport
            if original_viewport:
                try:
                    orig_w = original_viewport.get("w", 1280)
                    orig_h = original_viewport.get("h", 720)
                    await self._browser._page.set_viewport_size({"width": orig_w, "height": orig_h})
                    log.debug("zoom_tester.viewport_restored", width=orig_w, height=orig_h)
                except Exception:
                    pass

        return findings
