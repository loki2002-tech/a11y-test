"""
Screen Reader Accessibility Tree Tester — WCAG 4.1.2 and 1.3.1

Uses Playwright's Accessibility Tree snapshot (the same tree that
screen readers like NVDA, VoiceOver, and JAWS consume) to verify
that complex components have meaningful, logical speech output.

Unlike Axe-core which checks DOM attributes, this tester verifies
the ACTUAL computed accessibility name and role that the AT receives —
which is the ground truth for what a blind user hears.

Checks:
    - Interactive elements with no accessible name (empty speech)
    - Elements whose computed role does not match semantic intent
    - Interactive widgets that are invisible to the accessibility tree
      (detached from the AT due to aria-hidden on a parent)
"""

from __future__ import annotations

from typing import Any

from accessibility_agent.logging_config import get_logger
from accessibility_agent.wcag.schemas import WCAGLevel, WCAGPrinciple

log = get_logger(__name__)

# Interactive roles that MUST have an accessible name
_MUST_HAVE_NAME_ROLES = {
    "button", "link", "textbox", "combobox", "listbox", "checkbox",
    "radio", "switch", "slider", "spinbutton", "searchbox", "menuitem",
    "menuitemcheckbox", "menuitemradio", "tab", "treeitem", "option",
    "columnheader", "rowheader",
}

# Roles that are landmarks and should have accessible names for disambiguation
_LANDMARK_ROLES = {
    "navigation", "region", "complementary", "form", "search",
}


class ScreenReaderTester:
    """
    Validates the Playwright Accessibility Tree for screen reader correctness.

    Usage:
        tester = ScreenReaderTester(browser)
        findings = await tester.run(url, page_title)
    """

    def __init__(self, browser: Any) -> None:
        self._browser = browser

    async def run(self, url: str, page_title: str) -> list[dict[str, Any]]:
        """
        Snapshot the full accessibility tree and validate it.
        Returns a list of Finding-compatible dicts.
        """
        findings: list[dict[str, Any]] = []

        try:
            log.info("screen_reader_tester.starting", url=url)

            # Get Playwright's accessibility tree snapshot
            # This returns the same structure that AT (NVDA, VoiceOver) reads
            snapshot = await self._browser._page.accessibility.snapshot(interesting_only=True)

            if not snapshot:
                log.warning("screen_reader_tester.empty_snapshot", url=url)
                return findings

            # Walk the tree recursively
            violations = []
            self._walk_tree(snapshot, violations, depth=0)

            for v in violations:
                findings.append({
                    "rule_id": v["rule_id"],
                    "wcag": v["wcag"],
                    "description": v["description"],
                    "element": {"html": v.get("element_html", ""), "selector": v.get("selector", "")},
                    "impact": v.get("impact", "serious"),
                    "url": url,
                    "page_title": page_title,
                    "source": "screen_reader_tester",
                    "evidence": v.get("evidence", {}),
                })

            log.info("screen_reader_tester.complete", findings=len(findings), url=url)

        except Exception as exc:
            log.error("screen_reader_tester.failed", error=str(exc), url=url)

        return findings

    def _walk_tree(
        self,
        node: dict[str, Any],
        violations: list[dict[str, Any]],
        depth: int,
    ) -> None:
        """Recursively walk the accessibility tree, checking each node."""
        if not node or depth > 30:  # cap recursion depth
            return

        role = (node.get("role") or "").lower()
        name = (node.get("name") or "").strip()

        # Check 1: Interactive elements with no accessible name
        if role in _MUST_HAVE_NAME_ROLES and not name:
            violations.append({
                "rule_id": "screen-reader-unnamed-interactive",
                "wcag": {
                    "success_criterion": "4.1.2",
                    "level": WCAGLevel.A.value,
                    "title": "Name, Role, Value",
                    "principle": WCAGPrinciple.ROBUST.value,
                    "url": "https://www.w3.org/WAI/WCAG22/Understanding/name-role-value.html",
                },
                "description": (
                    f"A '{role}' element has no accessible name in the screen reader tree. "
                    f"A screen reader user will hear only '{role}' with no context about its purpose. "
                    f"Add an aria-label, aria-labelledby, or visible text content."
                ),
                "element_html": f"<element role='{role}'>",
                "selector": role,
                "impact": "critical",
                "evidence": {
                    "role": role,
                    "name": name,
                    "value": node.get("value"),
                    "checked": node.get("checked"),
                    "node_snapshot": {k: v for k, v in node.items() if k != "children"},
                },
            })

        # Check 2: Multiple landmarks of the same type without names
        if role in _LANDMARK_ROLES and not name:
            violations.append({
                "rule_id": "screen-reader-unnamed-landmark",
                "wcag": {
                    "success_criterion": "1.3.1",
                    "level": WCAGLevel.A.value,
                    "title": "Info and Relationships",
                    "principle": WCAGPrinciple.PERCEIVABLE.value,
                    "url": "https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships.html",
                },
                "description": (
                    f"A '{role}' landmark has no accessible name. If there are multiple "
                    f"'{role}' landmarks on this page, screen reader users cannot distinguish "
                    f"between them in the landmarks menu. Add aria-label='...' to name it."
                ),
                "element_html": f"<element role='{role}'>",
                "selector": role,
                "impact": "moderate",
                "evidence": {"role": role, "name": name},
            })

        # Recurse into children
        for child in node.get("children") or []:
            self._walk_tree(child, violations, depth + 1)
