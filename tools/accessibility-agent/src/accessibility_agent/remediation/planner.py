"""
Remediation Planner — generates a structured RemediationPlan using the LLM.

The planner receives:
    - The original Finding (what is wrong)
    - The SourceContext (what the source code looks like)
    - The classification (how safe it is to auto-fix)
    - WCAG context from the RAG engine

And produces:
    - A RemediationPlan (validated Pydantic model)
    - Exact target_attribute and target_value
    - risk_level and risk_assessment
    - reasoning chain

DESIGN DECISIONS:
    1. For SAFE_AUTO_FIX cases, the planner uses a deterministic template
       (no LLM call) — this is faster and avoids rate limits.
    2. For LIKELY_AUTO_FIX and AI_PROPOSED_FIX, the LLM is called.
    3. For MANUAL_REVIEW_REQUIRED / DO_NOT_AUTO_REMEDIATE, the planner
       generates a manual review package immediately (no LLM call).
    4. All LLM output is extracted as structured JSON and validated by Pydantic.
    5. If the LLM fails or produces invalid output, we fall back to a
       safe template plan rather than crashing.
"""

from __future__ import annotations

import json
import re
from typing import Any

from accessibility_agent.ai.llm_client import create_llm_client
from accessibility_agent.logging_config import get_logger
from accessibility_agent.remediation.schemas import (
    ProblemType,
    RemediationAutomationLevel,
    RemediationPlan,
    SourceContext,
    SourceLocation,
)
from accessibility_agent.wcag.rag_engine import RAGEngine

log = get_logger(__name__)

# ── Safe-fix templates (no LLM needed) ───────────────────────────────────────
# Keyed by (problem_type, element_tag).

_SAFE_TEMPLATES: dict[tuple[str, str], dict[str, str]] = {
    # html[lang] missing
    (ProblemType.MISSING_MARKUP.value, "html"): {
        "root_cause": "The <html> element is missing a lang attribute, which prevents screen readers from using the correct pronunciation engine.",
        "fix_strategy": "Add lang='en' (or the appropriate BCP-47 language tag) to the opening <html> element.",
        "target_attribute": "lang",
        "target_value": "en",
        "expected_change": "Add lang=\"en\" to <html>",
        "risk_level": "low",
        "risk_assessment": "Zero risk — adding lang is additive and has no visual or behavioral side-effects.",
    },
    # tabindex > 0
    (ProblemType.KEYBOARD_ACCESS.value, "*"): {
        "root_cause": "A positive tabindex value overrides the natural DOM tab order, creating a confusing and unpredictable keyboard navigation experience.",
        "fix_strategy": "Replace tabindex='[positive number]' with tabindex='0' to include the element in the natural tab order without disrupting it.",
        "target_attribute": "tabindex",
        "target_value": "0",
        "expected_change": "Replace positive tabindex with tabindex=\"0\"",
        "risk_level": "low",
        "risk_assessment": "Low risk — the element remains focusable; only the tab order is normalized.",
    },
    # aria-hidden on interactive
    (ProblemType.INCORRECT_ARIA.value, "button"): {
        "root_cause": "aria-hidden='true' is applied to an interactive button, hiding it entirely from assistive technology while it remains visible and functional on-screen.",
        "fix_strategy": "Remove the aria-hidden='true' attribute from the button element.",
        "target_attribute": "aria-hidden",
        "target_value": "",  # empty means remove
        "expected_change": "Remove aria-hidden=\"true\" from button",
        "risk_level": "low",
        "risk_assessment": "Low risk — restoring AT visibility to an element that is already visible on-screen.",
    },
    # Decorative image (inside interactive element)
    (ProblemType.IMAGE_ALT.value, "img"): {
        "root_cause": "A decorative image is missing an alt attribute. Without alt='', screen readers announce the filename, which is not meaningful.",
        "fix_strategy": "Add alt='' to mark the image as decorative (the parent element provides the accessible name).",
        "target_attribute": "alt",
        "target_value": "",
        "expected_change": "Add alt=\"\" to decorative image",
        "risk_level": "low",
        "risk_assessment": "Low risk — adding alt='' is the correct pattern for decorative images; no content meaning is lost.",
    },
}


class RemediationPlanner:
    """
    Generates a RemediationPlan for a given finding + source context.

    Usage:
        planner = RemediationPlanner()
        plan = await planner.plan(finding_data, location, source_context, classification)
    """

    def __init__(self, llm_client=None) -> None:
        self._llm = llm_client or create_llm_client()
        self._rag = RAGEngine()
        self._llm_enabled = self._llm.provider_name != "disabled"

    async def plan(
        self,
        finding_data: dict[str, Any],
        location: SourceLocation,
        source_context: SourceContext,
        automation_level: RemediationAutomationLevel,
        classification_confidence: float,
        classified_by: str,
        attempt_number: int = 1,
        previous_failure: str = "",
    ) -> RemediationPlan:
        """
        Generate a RemediationPlan.

        Args:
            finding_data:             The Finding dict
            location:                 SourceLocation from Phase 1
            source_context:           SourceContext from Phase 2 analyzer
            automation_level:         Classification from Phase 2 classifier
            classification_confidence: Classifier confidence
            classified_by:            'deterministic_rules' | 'llm'
            attempt_number:           1–3 (retry context)
            previous_failure:         Why the last attempt failed (for retry)

        Returns:
            RemediationPlan — always returns; never raises.
        """
        finding_id = finding_data.get("finding_id", "UNKNOWN")
        wcag_sc = finding_data.get("wcag", {}).get("success_criterion", "")
        wcag_level = finding_data.get("wcag", {}).get("level", "")
        wcag_title = finding_data.get("wcag", {}).get("title", "")

        log.info(
            "planner.start",
            finding_id=finding_id,
            automation_level=automation_level.value,
            attempt=attempt_number,
            llm_enabled=self._llm_enabled,
        )

        # ── MANUAL / DO_NOT_AUTO cases: no planning needed ────────────────────
        if automation_level in (
            RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED,
            RemediationAutomationLevel.DO_NOT_AUTO_REMEDIATE,
        ):
            return self._manual_review_plan(
                finding_id, finding_data, source_context,
                automation_level, classification_confidence, classified_by,
                wcag_sc, wcag_level, wcag_title,
            )

        # ── SAFE_AUTO_FIX: use deterministic template ─────────────────────────
        if automation_level == RemediationAutomationLevel.SAFE_AUTO_FIX:
            template_plan = self._try_template_plan(
                finding_id, source_context, automation_level,
                classification_confidence, classified_by,
                wcag_sc, wcag_level, wcag_title, attempt_number,
            )
            if template_plan:
                log.info("planner.template_used", finding_id=finding_id)
                return template_plan

        # ── LIKELY / AI_PROPOSED: call LLM ────────────────────────────────────
        if self._llm_enabled:
            llm_plan = await self._llm_plan(
                finding_id, finding_data, location, source_context,
                automation_level, classification_confidence, classified_by,
                wcag_sc, wcag_level, wcag_title, attempt_number, previous_failure,
            )
            if llm_plan:
                return llm_plan

        # ── LLM disabled or failed: use conservative fallback ─────────────────
        log.warning("planner.fallback_plan", finding_id=finding_id)
        return self._fallback_plan(
            finding_id, source_context, automation_level,
            classification_confidence, classified_by,
            wcag_sc, wcag_level, wcag_title, attempt_number,
        )

    # ── Template planner (no LLM) ─────────────────────────────────────────────

    def _try_template_plan(
        self,
        finding_id: str,
        ctx: SourceContext,
        automation_level: RemediationAutomationLevel,
        confidence: float,
        classified_by: str,
        wcag_sc: str,
        wcag_level: str,
        wcag_title: str,
        attempt_number: int,
    ) -> RemediationPlan | None:
        """Look up a deterministic fix template."""
        key = (ctx.problem_type.value, ctx.element_tag)
        template = _SAFE_TEMPLATES.get(key)
        if not template:
            # Try wildcard element tag
            key = (ctx.problem_type.value, "*")
            template = _SAFE_TEMPLATES.get(key)
        if not template:
            return None

        return RemediationPlan(
            finding_id=finding_id,
            attempt_number=attempt_number,
            automation_level=automation_level,
            classification_confidence=confidence,
            classified_by=classified_by,
            problem_type=ctx.problem_type,
            root_cause=template["root_cause"],
            fix_strategy=template["fix_strategy"],
            expected_change_description=template["expected_change"],
            target_attribute=template.get("target_attribute", ""),
            target_value=template.get("target_value", ""),
            risk_level=template.get("risk_level", "low"),
            risk_assessment=template.get("risk_assessment", ""),
            wcag_criterion=wcag_sc,
            wcag_level=wcag_level,
            wcag_title=wcag_title,
            reasoning=f"Deterministic template applied for {ctx.problem_type.value} on <{ctx.element_tag}>.",
            requires_manual_review=False,
        )

    # ── LLM planner ───────────────────────────────────────────────────────────

    async def _llm_plan(
        self,
        finding_id: str,
        finding_data: dict[str, Any],
        location: SourceLocation,
        ctx: SourceContext,
        automation_level: RemediationAutomationLevel,
        confidence: float,
        classified_by: str,
        wcag_sc: str,
        wcag_level: str,
        wcag_title: str,
        attempt_number: int,
        previous_failure: str,
    ) -> RemediationPlan | None:
        """Generate a plan via LLM with RAG-grounded WCAG context."""
        try:
            # Build RAG context
            wcag_context = self._rag.build_context_for_finding(finding_data)

            # Build planning prompt
            prompt = self._build_prompt(
                finding_data, location, ctx, wcag_context,
                automation_level, attempt_number, previous_failure,
            )

            log.info("planner.llm_call", finding_id=finding_id, attempt=attempt_number)
            response = await self._llm.generate(prompt=prompt)

            if not response or not response.text:
                log.warning("planner.llm_empty_response", finding_id=finding_id)
                return None

            raw = self._extract_json(response.text)
            if not raw:
                log.warning("planner.llm_json_parse_failed", finding_id=finding_id)
                return None

            # Validate required fields
            root_cause = raw.get("root_cause", "").strip()
            fix_strategy = raw.get("fix_strategy", "").strip()
            if len(root_cause) < 10 or len(fix_strategy) < 10:
                log.warning("planner.llm_incomplete_response", finding_id=finding_id)
                return None

            # Map risk level
            risk = str(raw.get("risk_level", "medium")).lower()
            if risk not in ("low", "medium", "high"):
                risk = "medium"

            # Map problem type
            problem_str = raw.get("problem_type", ctx.problem_type.value)
            try:
                problem_type = ProblemType(problem_str)
            except ValueError:
                problem_type = ctx.problem_type

            plan = RemediationPlan(
                finding_id=finding_id,
                attempt_number=attempt_number,
                automation_level=automation_level,
                classification_confidence=confidence,
                classified_by="llm",
                problem_type=problem_type,
                root_cause=root_cause,
                fix_strategy=fix_strategy,
                expected_change_description=raw.get("expected_change", ""),
                target_attribute=raw.get("target_attribute", ""),
                target_value=raw.get("target_value", ""),
                risk_level=risk,
                risk_assessment=raw.get("risk_assessment", ""),
                wcag_criterion=wcag_sc,
                wcag_level=wcag_level,
                wcag_title=wcag_title,
                requires_tests=raw.get("requires_tests", []),
                reasoning=raw.get("reasoning", response.text[:500]),
            )

            log.info(
                "planner.llm_plan_created",
                finding_id=finding_id,
                problem_type=problem_type.value,
                target_attribute=plan.target_attribute,
                risk_level=plan.risk_level,
            )
            return plan

        except Exception as exc:
            log.error("planner.llm_error", finding_id=finding_id, error=str(exc))
            return None

    # ── Manual review plan ────────────────────────────────────────────────────

    @staticmethod
    def _manual_review_plan(
        finding_id: str,
        finding_data: dict[str, Any],
        ctx: SourceContext,
        automation_level: RemediationAutomationLevel,
        confidence: float,
        classified_by: str,
        wcag_sc: str,
        wcag_level: str,
        wcag_title: str,
    ) -> RemediationPlan:
        """Return a plan that marks the finding as requiring manual review."""
        desc = finding_data.get("description", "Accessibility issue detected.")
        reason_map = {
            RemediationAutomationLevel.DO_NOT_AUTO_REMEDIATE: (
                "This type of issue (color contrast, payment flow, medical content) "
                "must never be automatically modified. Human designer/engineer review required."
            ),
            RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED: (
                "The correct fix requires understanding of content meaning, design intent, "
                "or domain context that the agent cannot safely determine automatically."
            ),
        }
        manual_reason = reason_map.get(automation_level, "Manual review required.")

        return RemediationPlan(
            finding_id=finding_id,
            automation_level=automation_level,
            classification_confidence=confidence,
            classified_by=classified_by,
            problem_type=ctx.problem_type,
            root_cause=desc,
            fix_strategy="Manual review required. See manual_review_reason for details.",
            risk_level="high",
            risk_assessment="Automated remediation is not safe for this issue type.",
            wcag_criterion=wcag_sc,
            wcag_level=wcag_level,
            wcag_title=wcag_title,
            requires_manual_review=True,
            manual_review_reason=manual_reason,
            reasoning=manual_reason,
        )

    # ── Fallback plan ─────────────────────────────────────────────────────────

    @staticmethod
    def _fallback_plan(
        finding_id: str,
        ctx: SourceContext,
        automation_level: RemediationAutomationLevel,
        confidence: float,
        classified_by: str,
        wcag_sc: str,
        wcag_level: str,
        wcag_title: str,
        attempt_number: int,
    ) -> RemediationPlan:
        """Conservative plan when LLM is unavailable or fails."""
        return RemediationPlan(
            finding_id=finding_id,
            attempt_number=attempt_number,
            automation_level=RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED,
            classification_confidence=confidence * 0.5,
            classified_by="fallback",
            problem_type=ctx.problem_type,
            root_cause=ctx.problem_summary or "Accessibility issue detected.",
            fix_strategy="LLM planning unavailable. Manual review recommended.",
            risk_level="medium",
            risk_assessment="Falling back to manual review because LLM planning failed.",
            wcag_criterion=wcag_sc,
            wcag_level=wcag_level,
            wcag_title=wcag_title,
            requires_manual_review=True,
            manual_review_reason="LLM planning failed or is disabled. Enable an LLM provider to generate automated fix plans.",
            reasoning="Fallback plan: LLM planning failed or is disabled.",
        )

    # ── Prompt builder ────────────────────────────────────────────────────────

    @staticmethod
    def _build_prompt(
        finding_data: dict[str, Any],
        location: SourceLocation,
        ctx: SourceContext,
        wcag_context: str,
        automation_level: RemediationAutomationLevel,
        attempt_number: int,
        previous_failure: str,
    ) -> str:
        """Build the LLM prompt for remediation planning."""
        element_html = finding_data.get("element", {}).get("html", "")[:300]
        description = finding_data.get("description", "")
        rule_id = finding_data.get("rule_id", "")

        retry_block = ""
        if attempt_number > 1 and previous_failure:
            retry_block = f"""
## Previous Attempt Failed (Attempt {attempt_number - 1})

The previous fix attempt failed for this reason:
{previous_failure}

You MUST produce a DIFFERENT fix strategy that avoids this failure.
"""

        return f"""You are a Senior Accessibility Engineer generating a precise remediation plan.

## Accessibility Finding

- **Rule ID**: {rule_id}
- **Description**: {description}
- **Element HTML**: `{element_html}`
- **Automation Level**: {automation_level.value} (pre-classified — do not override)
- **File**: `{location.file_path}` (line {location.start_line}–{location.end_line})
- **Language**: {ctx.language}
- **Framework**: {ctx.framework.value}

## Source Code Context

```{ctx.language}
{ctx.block_source}
```

## Element Analysis

- **Tag**: {ctx.element_tag}
- **Attributes**: {json.dumps(ctx.element_attributes)}
- **Is inside form**: {ctx.is_inside_form}
- **Is icon-only**: {ctx.is_icon_only}
- **Nearby labels**: {ctx.nearby_labels}
- **Has event handlers**: {ctx.has_event_handlers} ({', '.join(ctx.event_handler_names)})

{wcag_context}

{retry_block}

## Your Task

Produce a precise remediation plan as a JSON object. Rules:
1. The fix must be the MINIMUM change that resolves the violation.
2. Do not change unrelated code.
3. Do not invent content you cannot infer from the context.
4. The target_attribute and target_value must be the exact attribute name and value to add/modify.
5. If the fix is to REMOVE an attribute, set target_value to "" (empty string).

Respond with ONLY this JSON (no markdown, no explanation outside the JSON):

{{
  "problem_type": "{ctx.problem_type.value}",
  "root_cause": "Clear sentence explaining why this is a WCAG violation",
  "fix_strategy": "Exact description of the code change: what attribute/element to add/modify/remove",
  "expected_change": "One-line human summary, e.g. 'Add aria-label=\\"Register\\" to button on line 36'",
  "target_attribute": "The HTML attribute to add/modify, e.g. 'aria-label', 'lang', 'alt'",
  "target_value": "The exact value to set, or empty string to remove the attribute",
  "risk_level": "low|medium|high",
  "risk_assessment": "What could go wrong with this specific fix",
  "requires_tests": [],
  "reasoning": "Step-by-step reasoning for this fix choice"
}}"""

    # ── JSON extractor ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        """Extract a JSON object from LLM response (handles markdown code blocks)."""
        text = text.strip()
        # Strip markdown code fences
        for fence in ("```json", "```"):
            if text.startswith(fence):
                text = text[len(fence):]
                break
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON object with regex
            m = re.search(r"\{[\s\S]+\}", text)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
        return None
