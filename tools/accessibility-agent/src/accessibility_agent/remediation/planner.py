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
from accessibility_agent.ai.critic import CriticAgent
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

# ── Valid HTML/ARIA attribute names — used to validate LLM output ─────────────
# Any target_attribute not in this set (and not empty / __REPLACE_ELEMENT__) is rejected.
_VALID_HTML_ATTRIBUTES = {
    # ARIA
    "aria-label", "aria-labelledby", "aria-describedby", "aria-hidden",
    "aria-live", "aria-atomic", "aria-relevant", "aria-busy",
    "aria-required", "aria-invalid", "aria-expanded", "aria-selected",
    "aria-checked", "aria-pressed", "aria-disabled", "aria-readonly",
    "aria-multiselectable", "aria-orientation", "aria-haspopup",
    "aria-controls", "aria-owns", "aria-flowto", "aria-activedescendant",
    "aria-autocomplete", "aria-level", "aria-multiline", "aria-placeholder",
    "aria-roledescription", "aria-rowcount", "aria-rowindex", "aria-rowspan",
    "aria-colcount", "aria-colindex", "aria-colspan", "aria-setsize",
    "aria-posinset", "aria-valuemin", "aria-valuemax", "aria-valuenow",
    "aria-valuetext", "aria-modal", "aria-sort",
    # Common HTML
    "role", "lang", "alt", "title", "tabindex", "type", "value",
    "src", "href", "for", "id", "class", "name", "placeholder",
    "required", "disabled", "readonly", "checked", "selected",
    "multiple", "size", "maxlength", "minlength", "pattern",
    "action", "method", "enctype", "target", "rel", "download",
    "width", "height", "colspan", "rowspan", "scope", "headers",
    "summary", "caption", "abbr", "axis",
    "autocomplete", "autofocus", "autoplay", "controls", "loop", "muted",
    "poster", "preload", "kind", "srclang", "label", "default",
    "frameborder", "allowfullscreen", "sandbox", "loading",
    "decoding", "crossorigin", "integrity", "referrerpolicy",
    "data-*",  # wildcard — checked separately
    # Form
    "accept", "accept-charset", "enctype", "novalidate",
    "inputmode", "enterkeyhint", "spellcheck", "contenteditable",
    "draggable", "translate", "hidden",
    # WCAG / landmark specific
    "tabindex", "accesskey", "dir", "xml:lang",
}

# HTML tag names that LLMs sometimes incorrectly use as attribute names
_TAG_NAMES_MISTAKEN_AS_ATTRS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "div", "span", "section", "article", "aside", "main",
    "header", "footer", "nav", "form", "input", "button", "select",
    "textarea", "label", "table", "tr", "td", "th", "ul", "ol", "li",
    "marquee", "blink", "tag", "element", "content", "text",
    "heading", "paragraph", "anchor", "link",
}


def _is_valid_target_attribute(attr: str) -> bool:
    """Return True if attr is an acceptable target_attribute value."""
    if not attr:
        return True  # empty = element insertion mode
    if attr == "__REPLACE_ELEMENT__":
        return True
    # Allow pipe-separated multi-attribute: "role|aria-label"
    parts = attr.split("|")
    for part in parts:
        part = part.strip()
        if part in _TAG_NAMES_MISTAKEN_AS_ATTRS:
            return False
        if part in _VALID_HTML_ATTRIBUTES:
            continue
        if part.startswith("data-"):
            continue
        # Unknown attribute — allow it (real HTML has many attributes)
        # but reject tag names we know are wrong
        if re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", part):
            continue
        return False
    return True


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
        self._critic = CriticAgent(llm_client=self._llm)
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
        """Generate a plan via LLM with Chain-of-Thought reasoning + Critic review."""
        try:
            # Build RAG context
            wcag_context = self._rag.build_context_for_finding(finding_data)
            element_html = finding_data.get("element", {}).get("html", "")[:300]

            # ── PHASE 1: Chain-of-Thought Reasoning ──────────────────────────
            # Ask the LLM to think through the problem before generating a fix.
            # Higher temperature (0.3) here allows for broader reasoning.
            reasoning_prompt = self._build_reasoning_prompt(
                finding_data, location, ctx, wcag_context, automation_level,
                attempt_number, previous_failure,
            )
            log.info("planner.cot_reasoning_start", finding_id=finding_id, attempt=attempt_number)
            reasoning_response = await self._llm.generate(
                prompt=reasoning_prompt,
                temperature=0.3,
            )
            reasoning_text = reasoning_response.text if reasoning_response else ""
            if not reasoning_text:
                log.warning("planner.cot_reasoning_empty", finding_id=finding_id)
                reasoning_text = "No prior reasoning available."
            else:
                log.info("planner.cot_reasoning_complete", finding_id=finding_id, reasoning_len=len(reasoning_text))

            # ── PHASE 2: Action — Generate precise JSON fix ───────────────────
            # Very low temperature (0.05) for deterministic code generation.
            action_prompt = self._build_prompt(
                finding_data, location, ctx, wcag_context,
                automation_level, attempt_number, previous_failure,
                reasoning_context=reasoning_text,
            )
            log.info("planner.llm_action_call", finding_id=finding_id, attempt=attempt_number)
            response = await self._llm.generate(prompt=action_prompt, temperature=0.05)

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

            # ── CRITICAL: Validate target_attribute ──────────────────────────
            target_attr = raw.get("target_attribute", "").strip()
            target_val = raw.get("target_value", "").strip()

            if not _is_valid_target_attribute(target_attr):
                log.warning(
                    "planner.invalid_target_attribute_rejected",
                    finding_id=finding_id,
                    target_attribute=target_attr,
                )
                return self._manual_review_plan(
                    finding_id, finding_data, ctx,
                    RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED,
                    confidence * 0.5, "llm_validation_failed",
                    wcag_sc, wcag_level, wcag_title,
                )

            # ── PHASE 3: Critic Review ────────────────────────────────────────
            # A second LLM call reviews the proposed fix before it touches code.
            critic_result = await self._critic.review(
                original_html=element_html,
                proposed_fix=target_val if target_val else target_attr,
                wcag_rule=f"{wcag_sc} - {wcag_title}",
                finding_description=finding_data.get("description", ""),
                finding_id=finding_id,
            )
            if not critic_result.approved:
                log.warning(
                    "planner.critic_rejected",
                    finding_id=finding_id,
                    reason=critic_result.reason,
                )
                # Return None so the caller can retry with the critic's reason as context
                if attempt_number < 3:
                    # Inject critic feedback into next attempt via manual_review
                    return self._manual_review_plan(
                        finding_id, finding_data, ctx,
                        RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED,
                        confidence * 0.5, f"critic_rejected: {critic_result.reason}",
                        wcag_sc, wcag_level, wcag_title,
                    )
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
                target_attribute=target_attr,
                target_value=target_val,
                risk_level=risk,
                risk_assessment=raw.get("risk_assessment", ""),
                wcag_criterion=wcag_sc,
                wcag_level=wcag_level,
                wcag_title=wcag_title,
                requires_tests=raw.get("requires_tests", []),
                reasoning=f"[CoT]\n{reasoning_text[:300]}\n\n[Action]\n{raw.get('reasoning', response.text[:200])}",
            )

            log.info(
                "planner.llm_plan_created",
                finding_id=finding_id,
                problem_type=problem_type.value,
                target_attribute=plan.target_attribute,
                risk_level=plan.risk_level,
                critic_approved=True,
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
    def _build_reasoning_prompt(
        finding_data: dict[str, Any],
        location: SourceLocation,
        ctx: SourceContext,
        wcag_context: str,
        automation_level: RemediationAutomationLevel,
        attempt_number: int,
        previous_failure: str,
    ) -> str:
        """Phase 1 — Chain-of-Thought: ask the LLM to reason about the fix before acting."""
        element_html = finding_data.get("element", {}).get("html", "")[:300]
        description = finding_data.get("description", "")
        rule_id = finding_data.get("rule_id", "")

        return f"""You are a Senior Accessibility Engineer. Before writing any code, think carefully about this accessibility issue.

## Accessibility Violation
- **Rule ID**: {rule_id}
- **Description**: {description}
- **Element HTML**: `{element_html}`
- **File**: `{location.file_path}` (line {location.start_line})
- **Element tag**: {ctx.element_tag}
- **Attributes**: {json.dumps(ctx.element_attributes)}
- **Framework**: {ctx.framework.value}

{wcag_context}

## Think Through the Following Questions
1. What is the EXACT root cause of this WCAG violation?
2. What is the MINIMUM change required to fix it — one attribute, one element swap, or a new insertion?
3. Is there any risk that adding this fix could break another WCAG rule?
4. For the `target_attribute`: should I add/modify an attribute, replace the whole element, or insert new markup?
5. What should the exact `target_value` be, based on the surrounding code context?

Write your reasoning in plain English (3-5 sentences). Do NOT write any JSON or code yet. Just think."""

    @staticmethod
    def _build_prompt(
        finding_data: dict[str, Any],
        location: SourceLocation,
        ctx: SourceContext,
        wcag_context: str,
        automation_level: RemediationAutomationLevel,
        attempt_number: int,
        previous_failure: str,
        reasoning_context: str = "",
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
        reasoning_block = ""
        if reasoning_context:
            reasoning_block = f"""
## Your Prior Reasoning (Phase 1 — Chain-of-Thought)

You already analyzed this problem and concluded:
{reasoning_context[:600]}

Use this reasoning to guide your JSON fix below.
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

{reasoning_block}

## Your Task

Produce a precise remediation plan as a JSON object.

### CRITICAL RULES — READ ALL BEFORE RESPONDING:

**RULE 1 — target_attribute MUST be a real HTML/ARIA attribute name.**
VALID examples: `aria-label`, `lang`, `alt`, `role`, `for`, `tabindex`,
`aria-labelledby`, `aria-hidden`, `title`, `type`, `value`, `src`, `href`,
`scope`, `aria-live`, `aria-required`, `aria-describedby`
NEVER USE tag names as attributes: h1, h2, p, div, span, marquee, section, nav
NEVER USE made-up words: tag, element, content, text, heading, paragraph

**RULE 2 — To INSERT a new element (e.g. add an h1 heading, add a label):**
- Set `target_attribute` to `""` (empty string)
- Set `target_value` to the complete HTML to insert, e.g. `"<h1>ShopNow</h1>"`
- Set `problem_type` to `"missing_markup"`
- Describe WHERE to insert clearly in `fix_strategy`

**RULE 3 — To REPLACE one element with another (e.g. marquee → p, div → button):**
- Set `target_attribute` to `"__REPLACE_ELEMENT__"`
- Set `target_value` to the FULL replacement element with same text content,
  e.g. `"<p class=\\"marquee-text\\">Free shipping on orders over $50!</p>"`
- Set `problem_type` to `"missing_markup"`

**RULE 4 — To add ONE attribute (e.g. aria-label, lang, alt, role):**
- Set `target_attribute` to the exact attribute name (e.g. `"aria-label"`)
- Set `target_value` to the exact value (e.g. `"Search"`)

**RULE 5 — To add TWO attributes simultaneously (e.g. role + aria-label):**
- Set `target_attribute` to `"role|aria-label"` (pipe-separated)
- Set `target_value` to `"region|Hero Banner"` (pipe-separated, same order)

**RULE 6 — To REMOVE an attribute:** Set `target_value` to `"__REMOVE__"`.

**RULE 7 — Minimum change only.** Do not touch unrelated code.

Respond with ONLY this JSON (no markdown, no explanation outside the JSON):

{{
  "problem_type": "{ctx.problem_type.value}",
  "root_cause": "Clear sentence explaining why this is a WCAG violation",
  "fix_strategy": "Exact description: what element/attribute to add/modify/remove and where",
  "expected_change": "One-line human summary, e.g. 'Add aria-label=\\"Register\\" to button on line 36'",
  "target_attribute": "Real HTML/ARIA attribute, or empty string for element insert, or __REPLACE_ELEMENT__ for element swap, or pipe-separated for multi-attribute",
  "target_value": "Exact value, complete new element HTML, or __REMOVE__",
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
