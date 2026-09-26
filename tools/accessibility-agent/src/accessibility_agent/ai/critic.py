"""
Critic Agent — Second-Opinion LLM Reviewer for AI-generated accessibility fixes.

Architecture:
    Planner (generates fix) → Critic (reviews fix) → approved / rejected

The Critic is intentionally lightweight: it only validates whether the proposed
HTML change correctly satisfies the WCAG rule it was meant to fix, without
hallucinating new content. If the critic rejects the fix, the planner retries
with the critic's rejection reason as additional context.

Design decisions:
    1. The critic uses a VERY low temperature (0.05) — this is a logic task, not creative.
    2. The critic response is parsed as simple JSON: {"approved": bool, "reason": str}.
    3. If the critic itself fails (LLM error), we default to approved=True to avoid
       blocking the pipeline — the existing whitelist guards are already in place.
    4. The critic is only invoked for AI_PROPOSED_FIX and LIKELY_AUTO_FIX levels.
       SAFE_AUTO_FIX templates are pre-validated and don't need a second opinion.
"""

from __future__ import annotations

import json
import re
from typing import Any

from accessibility_agent.ai.llm_client import BaseLLMClient, create_llm_client
from accessibility_agent.logging_config import get_logger

log = get_logger(__name__)

# Temperature for critic — very low: this is a binary approval task, not creative writing
_CRITIC_TEMPERATURE = 0.05


class CriticResult:
    """Result of a Critic review."""

    def __init__(self, approved: bool, reason: str, raw_response: str = "") -> None:
        self.approved = approved
        self.reason = reason
        self.raw_response = raw_response

    def __repr__(self) -> str:
        return f"CriticResult(approved={self.approved}, reason={self.reason!r})"


class CriticAgent:
    """
    Lightweight second-opinion reviewer.

    Usage:
        critic = CriticAgent()
        result = await critic.review(
            original_html="<button>Submit</button>",
            proposed_fix='<button aria-label="Submit form">Submit</button>',
            wcag_rule="4.1.2 - Name, Role, Value",
            finding_description="Button does not have an accessible name.",
        )
        if not result.approved:
            # retry with result.reason as context
    """

    def __init__(self, llm_client: BaseLLMClient | None = None) -> None:
        self._llm = llm_client or create_llm_client()

    async def review(
        self,
        original_html: str,
        proposed_fix: str,
        wcag_rule: str,
        finding_description: str,
        finding_id: str = "UNKNOWN",
    ) -> CriticResult:
        """
        Ask the LLM to critically review a proposed HTML fix.

        Returns CriticResult with approved=True/False and a reason.
        On LLM failure, defaults to approved=True (non-blocking).
        """
        if self._llm.provider_name == "disabled":
            log.debug("critic.skipped_disabled_llm", finding_id=finding_id)
            return CriticResult(approved=True, reason="LLM disabled — critic skipped.")

        prompt = self._build_prompt(original_html, proposed_fix, wcag_rule, finding_description)

        log.info("critic.reviewing", finding_id=finding_id)
        try:
            response = await self._llm.generate(prompt=prompt, temperature=_CRITIC_TEMPERATURE)

            if not response or not response.text:
                log.warning("critic.empty_response", finding_id=finding_id)
                return CriticResult(approved=True, reason="Critic returned empty response — defaulting to approved.")

            result = self._parse_response(response.text)
            log.info(
                "critic.verdict",
                finding_id=finding_id,
                approved=result.approved,
                reason=result.reason[:120],
            )
            return result

        except Exception as exc:
            log.error("critic.failed", finding_id=finding_id, error=str(exc))
            return CriticResult(approved=True, reason=f"Critic error ({exc}) — defaulting to approved.")

    @staticmethod
    def _build_prompt(
        original_html: str,
        proposed_fix: str,
        wcag_rule: str,
        finding_description: str,
    ) -> str:
        return f"""You are a hostile, adversarial Senior WCAG Accessibility Auditor.
Your ONLY job is to find reasons why the proposed fix is WRONG.

## The Accessibility Violation
- **WCAG Rule**: {wcag_rule}
- **Description**: {finding_description}

## Original HTML
```html
{original_html[:500]}
```

## Proposed Fix
```html
{proposed_fix[:500]}
```

## Adversarial Review Checklist — Check ALL of these:

1. **False positive risk**: Does the original HTML already satisfy the requirement through a mechanism not shown here (aria-labelledby, native label, visible text, title, semantic role)?

2. **Accessible name conflict**: Does the proposed fix introduce an `aria-label` that conflicts with visible text content? Would a screen reader announce a name different from what the user sees?

3. **ARIA validity**: Is every ARIA attribute, role, and property valid? Are referenced IDs guaranteed to exist in the DOM?

4. **Native HTML preference violation**: Is this fix using ARIA when a simpler, more robust native HTML fix was available (e.g., using `aria-label` when a `<label>` element would be better)?

5. **Complex widget incompleteness**: If this is an interactive control (accordion, modal, tab, combobox), does this fix address the COMPLETE interaction model — semantics, keyboard, focus, and state — or only a superficial attribute?

6. **State attribute accuracy**: If the fix adds `aria-expanded`, `aria-selected`, `aria-checked`, etc., is there proof that JavaScript maintains this state at runtime? Static HTML with hardcoded state attributes is often WRONG.

7. **Regression risk**: Could this fix break existing accessibility features that were previously working? Could it remove a correct role, override a correct name, or hide content from assistive technology?

8. **Syntax validity**: Is the proposed HTML syntactically valid? Are there unclosed tags, malformed attributes, or duplicate attribute names?

9. **No-op check**: Is the proposed fix identical to the original (or does it change something irrelevant)?

10. **Placeholder/lorem values**: Does the fix use placeholder text like "TODO", "FIXME", "your-label-here", or "lorem ipsum"?

## Your verdict

If you find ANY credible, unresolved problem → reject the fix.
A "credible problem" is one that you can specifically name and that is not clearly addressed by the proposed change.
Do not reject based on hypothetical edge cases that are clearly out of scope.

Respond with ONLY this JSON (no markdown, no text outside):
{{
  "approved": true,
  "reason": "Specific technical justification for approval or rejection"
}}"""

    @staticmethod
    def _parse_response(text: str) -> CriticResult:
        """Parse the critic's JSON response."""
        text = text.strip()
        # Strip markdown fences
        for fence in ("```json", "```"):
            if text.startswith(fence):
                text = text[len(fence):]
                break
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try regex extraction
            m = re.search(r"\{[\s\S]+\}", text)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    return CriticResult(approved=True, reason="Could not parse critic response — defaulting to approved.")
            else:
                return CriticResult(approved=True, reason="No JSON in critic response — defaulting to approved.")

        approved = bool(data.get("approved", True))
        reason = str(data.get("reason", "No reason provided."))
        return CriticResult(approved=approved, reason=reason, raw_response=text)
