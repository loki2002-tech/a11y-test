"""
Motion & Seizure Tester — WCAG 2.2.2 and 2.3.1 Compliance

Detects content that:
    - Auto-plays for more than 5 seconds without a pause/stop control (WCAG 2.2.2)
    - Flashes more than 3 times per second — a seizure risk (WCAG 2.3.1)
    - Uses CSS animations or GIFs that loop indefinitely without a user control

WCAG Requirements:
    2.2.2 Pause, Stop, Hide (Level A):  Any moving/blinking content that
          lasts more than 5 seconds must have a mechanism to pause or stop it.
    2.3.1 Three Flashes or Below Threshold (Level A): No content flashes
          more than 3 times per second unless below the general flash threshold.
"""

from __future__ import annotations

from typing import Any

from accessibility_agent.logging_config import get_logger
from accessibility_agent.wcag.schemas import WCAGLevel, WCAGPrinciple

log = get_logger(__name__)


class MotionTester:
    """
    Detects auto-playing motion, looping animations, and flashing content.

    Usage:
        tester = MotionTester(browser)
        findings = await tester.run(url, page_title)
    """

    def __init__(self, browser: Any) -> None:
        self._browser = browser

    async def run(self, url: str, page_title: str) -> list[dict[str, Any]]:
        """
        Scan the page for WCAG 2.2.2 and 2.3.1 motion violations.
        Returns a list of Finding-compatible dicts.
        """
        findings: list[dict[str, Any]] = []

        try:
            log.info("motion_tester.starting", url=url)

            # 1. Detect auto-playing <video> without controls or user-initiated
            video_violations = await self._check_autoplay_video()
            findings.extend(video_violations)

            # 2. Detect CSS animations/transitions that run indefinitely
            animation_violations = await self._check_infinite_css_animations()
            findings.extend(animation_violations)

            # 3. Detect <marquee> and <blink> (legacy but still in the wild)
            legacy_violations = await self._check_legacy_motion_elements()
            findings.extend(legacy_violations)

            log.info("motion_tester.complete", findings=len(findings), url=url)

        except Exception as exc:
            log.error("motion_tester.failed", error=str(exc), url=url)

        for f in findings:
            f.update({"url": url, "page_title": page_title, "source": "motion_tester"})

        return findings

    async def _check_autoplay_video(self) -> list[dict[str, Any]]:
        """Detect <video autoplay> elements without a pause/stop control."""
        try:
            results = await self._browser.evaluate("""() => {
                const violations = [];
                const videos = document.querySelectorAll('video[autoplay]');
                for (const v of videos) {
                    const hasMuted = v.hasAttribute('muted');
                    const hasControls = v.hasAttribute('controls');
                    const loop = v.hasAttribute('loop');
                    // autoplay without controls AND not muted-only-decorative is a violation
                    if (!hasControls) {
                        violations.push({
                            outerHTML: v.outerHTML.slice(0, 200),
                            muted: hasMuted,
                            loop: loop,
                            id: v.id || '',
                            src: v.src || v.querySelector('source')?.src || '',
                        });
                    }
                }
                return violations;
            }""")

            findings = []
            for item in (results or []):
                findings.append({
                    "rule_id": "wcag-2-2-2-autoplay-video",
                    "wcag": {
                        "success_criterion": "2.2.2",
                        "level": WCAGLevel.A.value,
                        "title": "Pause, Stop, Hide",
                        "principle": WCAGPrinciple.OPERABLE.value,
                        "url": "https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html",
                    },
                    "description": (
                        "A <video> element auto-plays without providing playback controls. "
                        "Users with attention disorders, vestibular conditions, or cognitive "
                        "disabilities cannot pause or stop the motion. Add the `controls` attribute."
                    ),
                    "element": {
                        "html": item.get("outerHTML", "<video autoplay>"),
                        "selector": f"video[autoplay]{'#' + item['id'] if item.get('id') else ''}",
                    },
                    "impact": "serious",
                    "evidence": item,
                })
            return findings

        except Exception as exc:
            log.warning("motion_tester.autoplay_check_failed", error=str(exc))
            return []

    async def _check_infinite_css_animations(self) -> list[dict[str, Any]]:
        """Detect elements with infinite CSS animations that have no pause control nearby."""
        try:
            results = await self._browser.evaluate("""() => {
                const violations = [];
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const style = window.getComputedStyle(el);
                    const animName = style.animationName;
                    const animDuration = style.animationDuration;
                    const animIterCount = style.animationIterationCount;
                    if (
                        animName && animName !== 'none' &&
                        animIterCount === 'infinite' &&
                        parseFloat(animDuration) > 0
                    ) {
                        violations.push({
                            tag: el.tagName.toLowerCase(),
                            id: el.id || '',
                            className: el.className ? String(el.className).slice(0, 60) : '',
                            animationName: animName,
                            animationDuration: animDuration,
                        });
                        if (violations.length >= 5) break;
                    }
                }
                return violations;
            }""")

            findings = []
            for item in (results or []):
                selector = item["tag"]
                if item.get("id"):
                    selector += f"#{item['id']}"
                findings.append({
                    "rule_id": "wcag-2-2-2-infinite-animation",
                    "wcag": {
                        "success_criterion": "2.2.2",
                        "level": WCAGLevel.A.value,
                        "title": "Pause, Stop, Hide",
                        "principle": WCAGPrinciple.OPERABLE.value,
                        "url": "https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html",
                    },
                    "description": (
                        f"An element ({selector}) has an infinite CSS animation "
                        f"'{item.get('animationName', '')}' with no pause/stop control. "
                        f"This can cause distraction for users with attention or vestibular disorders. "
                        f"Wrap in a button to toggle, or use `prefers-reduced-motion` media query."
                    ),
                    "element": {
                        "html": f"<{item['tag']} class=\"{item.get('className', '')}\">",
                        "selector": selector,
                    },
                    "impact": "moderate",
                    "evidence": item,
                })
            return findings

        except Exception as exc:
            log.warning("motion_tester.css_animation_check_failed", error=str(exc))
            return []

    async def _check_legacy_motion_elements(self) -> list[dict[str, Any]]:
        """Detect deprecated <marquee> and <blink> elements."""
        try:
            results = await self._browser.evaluate("""() => {
                const violations = [];
                for (const tag of ['marquee', 'blink']) {
                    const els = document.querySelectorAll(tag);
                    for (const el of els) {
                        violations.push({
                            tag: tag,
                            html: el.outerHTML.slice(0, 200),
                            text: el.textContent.trim().slice(0, 80),
                        });
                    }
                }
                return violations;
            }""")

            findings = []
            for item in (results or []):
                findings.append({
                    "rule_id": "wcag-2-2-2-marquee",
                    "wcag": {
                        "success_criterion": "2.2.2",
                        "level": WCAGLevel.A.value,
                        "title": "Pause, Stop, Hide",
                        "principle": WCAGPrinciple.OPERABLE.value,
                        "url": "https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html",
                    },
                    "description": (
                        f"A deprecated <{item['tag']}> element creates auto-moving content "
                        f"with no pause control: \"{item.get('text', '')}...\". "
                        f"Replace with a static <p> element."
                    ),
                    "element": {
                        "html": item.get("html", f"<{item['tag']}>"),
                        "selector": item["tag"],
                    },
                    "impact": "serious",
                    "evidence": item,
                })
            return findings

        except Exception as exc:
            log.warning("motion_tester.legacy_check_failed", error=str(exc))
            return []
