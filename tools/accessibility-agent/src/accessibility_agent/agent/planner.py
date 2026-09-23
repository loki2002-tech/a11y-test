"""
Agent Planner — Smart Observe/Plan/Act Decision Engine (v0.6)

Improvements over v0.5:
- Feeds the Accessibility Tree (ARIA roles/labels/states) to the LLM instead of raw HTML.
  This is what screen readers actually see, making the AI decisions far more relevant.
- Tracks clicked selectors to avoid infinite loops.
- Generates a structured Test Plan (list of planned interactions with reasons) BEFORE
  any clicking begins. The plan is stored in ScanResult.scan_plan and shown in the report.
- Separates planning (what to test) from execution (what to click next).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from accessibility_agent.ai.llm_client import create_llm_client
from accessibility_agent.ai.prompts import build_smart_planner_prompt, build_test_plan_prompt
from accessibility_agent.logging_config import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)


class AgentPlanner:
    """
    Analyzes the page accessibility tree to:
    1. Generate a Test Plan (what the agent intends to test and why).
    2. Decide the next interaction to perform in the Observe -> Plan -> Act loop.
    """

    def __init__(self, llm_client=None):
        self._llm = llm_client or create_llm_client()
        self.is_enabled = self._llm.provider_name != "disabled"

    async def generate_test_plan(self, ax_tree: dict[str, Any], page_html: str) -> list[dict[str, Any]]:
        """
        Generate a structured test plan BEFORE any interactions begin.

        Returns a list of planned steps, each being:
        {
            "step": 1,
            "action": "click" | "fill_form" | "press_tab" | "run_axe",
            "target": "CSS selector or description",
            "reason": "Why this interaction is important for accessibility",
            "wcag_criteria": ["2.1.1", "4.1.2"]
        }

        Returns an empty list if LLM is disabled or fails.
        """
        if not self.is_enabled:
            return []

        ax_summary = self._summarize_ax_tree(ax_tree)
        prompt = build_test_plan_prompt(ax_summary, page_html[:5000])

        try:
            log.info("planner.generating_test_plan", provider=self._llm.provider_name)
            response = await self._llm.generate(prompt=prompt)
            if not response or not response.text:
                return []

            data = self._extract_json(response.text)
            plan = data.get("test_plan", [])
            log.info("planner.test_plan_generated", steps=len(plan))
            return plan[:10]  # Cap at 10 planned steps

        except Exception as exc:
            log.error("planner.test_plan_failed", error=str(exc))
            return []

    async def get_next_interactions(
        self,
        ax_tree: dict[str, Any],
        page_html: str,
        already_clicked: set[str],
    ) -> list[str]:
        """
        Ask the LLM for the next CSS selectors to click, using the accessibility
        tree for smarter, screen-reader-aware decision making.

        Returns an empty list if LLM is disabled or fails.
        """
        if not self.is_enabled:
            return []

        ax_summary = self._summarize_ax_tree(ax_tree)
        prompt = build_smart_planner_prompt(ax_summary, page_html[:8000], list(already_clicked))

        try:
            log.debug("planner.requesting_actions", provider=self._llm.provider_name)
            response = await self._llm.generate(prompt=prompt)
            if not response or not response.text:
                return []

            data = self._extract_json(response.text)
            selectors = data.get("selectors", [])

            # Filter out already clicked selectors
            new_selectors = [s for s in selectors if s not in already_clicked]
            return [str(s) for s in new_selectors][:5]

        except Exception as exc:
            log.error("planner.failed", error=str(exc))
            return []

    def _summarize_ax_tree(self, ax_tree: dict[str, Any]) -> str:
        """
        Flatten the browser accessibility tree into a compact, readable summary.

        Instead of sending the full nested JSON (which is massive), we extract
        only what the LLM needs: role, name, state (expanded/collapsed), and level.
        """
        lines: list[str] = []

        def _walk(node: dict[str, Any], depth: int = 0) -> None:
            if depth > 6:  # Limit recursion depth
                return
            role = node.get("role", "")
            name = node.get("name", "")
            value = node.get("value", "")
            checked = node.get("checked")
            expanded = node.get("expanded")
            disabled = node.get("disabled", False)
            required = node.get("required", False)

            if not role or role in ("none", "presentation", "generic"):
                for child in node.get("children", []):
                    _walk(child, depth)
                return

            state_parts = []
            if expanded is True:
                state_parts.append("expanded")
            elif expanded is False:
                state_parts.append("collapsed")
            if checked is True:
                state_parts.append("checked")
            if disabled:
                state_parts.append("disabled")
            if required:
                state_parts.append("required")

            state_str = f" [{', '.join(state_parts)}]" if state_parts else ""
            name_str = f' "{name}"' if name else ""
            value_str = f' = "{value}"' if value else ""

            indent = "  " * depth
            lines.append(f"{indent}{role}{name_str}{value_str}{state_str}")

            for child in node.get("children", []):
                _walk(child, depth + 1)

        _walk(ax_tree)
        summary = "\n".join(lines[:200])  # Cap at 200 lines
        return summary if summary else "Accessibility tree is empty."

    def _extract_json(self, text: str) -> dict:
        """Extract JSON from potential markdown code blocks."""
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            log.warning("planner.json_parse_failed", raw=text[:200])
            return {}
