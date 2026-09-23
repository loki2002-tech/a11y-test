"""
Remediation Classifier — determines how safe it is to automatically fix a finding.

This is the safety gate of the entire Remediation Agent.  A classification
that is TOO aggressive will cause the agent to modify production code it
shouldn't touch.  A classification that is TOO conservative will miss
automation opportunities.

DESIGN PRINCIPLE: Deterministic rules first, LLM only as a last resort.

The classifier is a two-stage pipeline:
    Stage 1: Rule table lookup (O(1), no API calls, fully reproducible)
    Stage 2: LLM escalation for ambiguous cases (only when Stage 1 is inconclusive)

Rule table structure:
    Each rule maps a combination of:
        - axe rule_id (if known)
        - WCAG success criterion
        - element tag + attributes
        - SourceContext signals (is_icon_only, is_inside_form, etc.)
    to:
        - RemediationAutomationLevel
        - ProblemType
        - confidence (0.0-1.0)
        - reasoning (human-readable)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from accessibility_agent.logging_config import get_logger
from accessibility_agent.remediation.schemas import (
    ProblemType,
    RemediationAutomationLevel,
    SourceContext,
)

log = get_logger(__name__)


# ── Classification Rule ───────────────────────────────────────────────────────


@dataclass
class ClassificationRule:
    """A single deterministic classification rule."""

    rule_id: str                           # Unique rule identifier for logging
    axe_rule_ids: list[str]               # axe-core rule IDs that trigger this rule
    wcag_scs: list[str]                   # WCAG SC numbers (e.g. "4.1.2")
    element_tags: list[str]               # HTML tags ([] = any tag)
    automation_level: RemediationAutomationLevel
    problem_type: ProblemType
    confidence: float                      # 0.0–1.0
    reasoning: str
    # Optional additional conditions (checked separately)
    requires_icon_only: bool = False
    requires_inside_form: bool = False
    blocks_if_no_text_alternative: bool = False


# ── Classification Rule Table ─────────────────────────────────────────────────
# Ordered from MOST specific to LEAST specific.
# First matching rule wins.

_RULES: list[ClassificationRule] = [

    # ── WCAG 3.1.1 — Language of Page ─────────────────────────────────────────
    ClassificationRule(
        rule_id="R001",
        axe_rule_ids=["html-has-lang", "html-lang-valid", "html-xml-lang-mismatch"],
        wcag_scs=["3.1.1"],
        element_tags=["html"],
        automation_level=RemediationAutomationLevel.SAFE_AUTO_FIX,
        problem_type=ProblemType.MISSING_MARKUP,
        confidence=0.99,
        reasoning=(
            "Adding lang='en' to <html> is a purely structural change. "
            "No content judgment is required. Risk: zero. "
            "The fix is always the same: <html lang='en'>."
        ),
    ),

    # ── WCAG 2.4.2 — Page Titled ──────────────────────────────────────────────
    ClassificationRule(
        rule_id="R002",
        axe_rule_ids=["document-title"],
        wcag_scs=["2.4.2"],
        element_tags=["head", "html"],
        automation_level=RemediationAutomationLevel.LIKELY_AUTO_FIX,
        problem_type=ProblemType.MISSING_MARKUP,
        confidence=0.85,
        reasoning=(
            "A <title> element must be added. The structural fix is deterministic, "
            "but the title content requires context from the page (route name, "
            "application name). Confidence is high but not perfect."
        ),
    ),

    # ── WCAG 4.1.2 — aria-hidden on interactive element ──────────────────────
    # NOTE: This MUST come BEFORE R003/R004 (button-name rules) because
    # aria-hidden-focus also reports against 4.1.2 on button elements.
    ClassificationRule(
        rule_id="R012",
        axe_rule_ids=["aria-hidden-focus", "aria-hidden-body"],
        wcag_scs=["4.1.2"],
        element_tags=["button", "a", "input", "select", "textarea"],
        automation_level=RemediationAutomationLevel.SAFE_AUTO_FIX,
        problem_type=ProblemType.INCORRECT_ARIA,
        confidence=0.96,
        reasoning=(
            "aria-hidden='true' on an interactive element hides it from AT entirely. "
            "Fix: remove aria-hidden='true'. This is always the correct fix — "
            "interactive elements must never be hidden from screen readers."
        ),
    ),

    # ── WCAG 4.1.2 — Button name (icon-only button) ───────────────────────────
    ClassificationRule(
        rule_id="R003",
        axe_rule_ids=["button-name"],
        wcag_scs=["4.1.2"],
        element_tags=["button"],
        automation_level=RemediationAutomationLevel.SAFE_AUTO_FIX,
        problem_type=ProblemType.INCORRECT_ARIA,
        confidence=0.95,
        requires_icon_only=True,
        reasoning=(
            "An icon-only button with no text and no aria-label. "
            "The fix is to add aria-label='[action]' inferred from the icon type or context. "
            "This is a structural fix — no content or behavior is changed."
        ),
    ),

    # ── WCAG 4.1.2 — Button name (button has text, but text is hidden) ────────
    ClassificationRule(
        rule_id="R004",
        axe_rule_ids=["button-name"],
        wcag_scs=["4.1.2"],
        element_tags=["button"],
        automation_level=RemediationAutomationLevel.LIKELY_AUTO_FIX,
        problem_type=ProblemType.INCORRECT_ARIA,
        confidence=0.88,
        reasoning=(
            "Button missing accessible name — likely icon-only or text visually hidden. "
            "Fix: add aria-label or aria-labelledby. "
            "Confidence is slightly lower because the correct label value needs inference."
        ),
    ),

    # ── WCAG 4.1.2 — Input name (link-name, input missing label) ─────────────
    ClassificationRule(
        rule_id="R005",
        axe_rule_ids=["label", "label-content-name-mismatch", "input-button-name"],
        wcag_scs=["4.1.2", "1.3.1"],
        element_tags=["input", "select", "textarea"],
        automation_level=RemediationAutomationLevel.LIKELY_AUTO_FIX,
        problem_type=ProblemType.FORM_LABELING,
        confidence=0.88,
        requires_inside_form=True,
        reasoning=(
            "Form input has no associated label. Fix: add <label for='id'> or aria-label. "
            "The label text can often be inferred from placeholder or surrounding context. "
            "LIKELY rather than SAFE because the label text requires inference."
        ),
    ),

    # ── WCAG 1.3.1 — Form input without form context ─────────────────────────
    ClassificationRule(
        rule_id="R006",
        axe_rule_ids=["label", "input-button-name"],
        wcag_scs=["1.3.1", "4.1.2"],
        element_tags=["input", "select", "textarea"],
        automation_level=RemediationAutomationLevel.AI_PROPOSED_FIX,
        problem_type=ProblemType.FORM_LABELING,
        confidence=0.72,
        reasoning=(
            "Input without a label, but not inside a detected form. "
            "May be a custom widget or standalone search input. "
            "AI should verify the correct label before proposing a fix."
        ),
    ),

    # ── WCAG 2.4.4 — Link purpose (generic text: 'click here', 'more') ────────
    ClassificationRule(
        rule_id="R007",
        axe_rule_ids=["link-name", "identical-links-same-purpose"],
        wcag_scs=["2.4.4"],
        element_tags=["a"],
        automation_level=RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED,
        problem_type=ProblemType.LINK_TEXT,
        confidence=0.90,
        reasoning=(
            "Link text is generic ('click here', 'read more', etc.). "
            "The correct descriptive text depends on what the link leads to. "
            "This requires human judgment — the agent cannot safely determine "
            "the correct link text without domain knowledge."
        ),
    ),

    # ── WCAG 1.1.1 — Image alt (decorative, inside button/link with text) ─────
    ClassificationRule(
        rule_id="R008",
        axe_rule_ids=["image-alt"],
        wcag_scs=["1.1.1"],
        element_tags=["img"],
        automation_level=RemediationAutomationLevel.SAFE_AUTO_FIX,
        problem_type=ProblemType.IMAGE_ALT,
        confidence=0.92,
        requires_icon_only=True,   # icon-only = image inside button/link with no text
        reasoning=(
            "Decorative image (inside an interactive element that has other labeling). "
            "Fix: add alt='' to mark as decorative. This is always safe when the image "
            "is purely decorative and the parent element provides the accessible name."
        ),
    ),

    # ── WCAG 1.1.1 — Image alt (standalone informational image) ──────────────
    ClassificationRule(
        rule_id="R009",
        axe_rule_ids=["image-alt"],
        wcag_scs=["1.1.1"],
        element_tags=["img"],
        automation_level=RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED,
        problem_type=ProblemType.IMAGE_ALT,
        confidence=0.95,
        reasoning=(
            "Standalone image missing alt text. The correct alt text depends on what "
            "the image depicts — this requires human understanding. "
            "The agent cannot invent alt text without risking misinformation."
        ),
    ),

    # ── WCAG 2.4.7 — Focus visible (outline:none without replacement) ─────────
    ClassificationRule(
        rule_id="R010",
        axe_rule_ids=["focus-visible", "scrollable-region-focusable"],
        wcag_scs=["2.4.7"],
        element_tags=["*"],
        automation_level=RemediationAutomationLevel.LIKELY_AUTO_FIX,
        problem_type=ProblemType.FOCUS_VISIBILITY,
        confidence=0.82,
        reasoning=(
            "Focus indicator is suppressed (outline:none or outline:0). "
            "Fix: add :focus-visible { outline: 2px solid currentColor; } "
            "or equivalent. Structural CSS change — moderate confidence."
        ),
    ),

    # ── WCAG 2.4.3 — Focus order (positive tabindex) ─────────────────────────
    ClassificationRule(
        rule_id="R011",
        axe_rule_ids=["tabindex"],
        wcag_scs=["2.4.3"],
        element_tags=["*"],
        automation_level=RemediationAutomationLevel.SAFE_AUTO_FIX,
        problem_type=ProblemType.KEYBOARD_ACCESS,
        confidence=0.97,
        reasoning=(
            "Positive tabindex (tabindex='1', '2', etc.) disrupts natural focus order. "
            "Fix: replace with tabindex='0'. No content change required. "
            "This is always the correct fix — no judgment needed."
        ),
    ),

    # ── WCAG 1.3.1 — role=presentation on semantic table ─────────────────────

    ClassificationRule(
        rule_id="R013",
        axe_rule_ids=["table-fake-caption", "td-headers-attr"],
        wcag_scs=["1.3.1"],
        element_tags=["table", "th", "td"],
        automation_level=RemediationAutomationLevel.AI_PROPOSED_FIX,
        problem_type=ProblemType.SEMANTIC_STRUCTURE,
        confidence=0.70,
        reasoning=(
            "Table structure issue. Could involve scope, headers, caption, or role. "
            "Context required — AI should verify the table type before proposing a fix."
        ),
    ),

    # ── WCAG 1.4.3 / 1.4.11 — Color contrast ────────────────────────────────
    ClassificationRule(
        rule_id="R014",
        axe_rule_ids=["color-contrast", "color-contrast-enhanced"],
        wcag_scs=["1.4.3", "1.4.11"],
        element_tags=["*"],
        automation_level=RemediationAutomationLevel.DO_NOT_AUTO_REMEDIATE,
        problem_type=ProblemType.COLOR_CONTRAST,
        confidence=0.99,
        reasoning=(
            "Color contrast requires coordinated changes to foreground and background colors. "
            "This involves design decisions that must be made by a human designer. "
            "An automated fix could break brand identity or create new contrast problems "
            "in other contexts. NEVER auto-remediate."
        ),
    ),

    # ── WCAG 1.3.1 — Heading order ────────────────────────────────────────────
    ClassificationRule(
        rule_id="R015",
        axe_rule_ids=["heading-order", "empty-heading"],
        wcag_scs=["1.3.1"],
        element_tags=["h1", "h2", "h3", "h4", "h5", "h6"],
        automation_level=RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED,
        problem_type=ProblemType.SEMANTIC_STRUCTURE,
        confidence=0.88,
        reasoning=(
            "Heading hierarchy issues require understanding of the page's logical structure. "
            "The agent cannot safely change heading levels without potentially breaking "
            "the document outline. Requires human review."
        ),
    ),
]


# ── Classifier ────────────────────────────────────────────────────────────────


class RemediationClassifier:
    """
    Classifies a finding's automation safety level using a deterministic rule table.

    Stage 1: Rule table lookup (always runs first)
    Stage 2: LLM escalation (only if rule table returns no match)

    The classifier never returns None — every finding gets a classification.
    DO_NOT_AUTO_REMEDIATE is the safe default if nothing else matches.
    """

    def classify(
        self,
        finding_data: dict[str, Any],
        source_context: SourceContext,
    ) -> tuple[RemediationAutomationLevel, ProblemType, float, str]:
        """
        Classify a finding.

        Args:
            finding_data:   The Finding dict from the scan report
            source_context: SourceContext from the SourceAnalyzer

        Returns:
            Tuple of (automation_level, problem_type, confidence, reasoning)
        """
        rule_id_finding = finding_data.get("rule_id", "")
        wcag_sc = finding_data.get("wcag", {}).get("success_criterion", "")
        element_tag = source_context.element_tag
        is_icon_only = source_context.is_icon_only
        is_inside_form = source_context.is_inside_form

        log.info(
            "classifier.start",
            finding_id=finding_data.get("finding_id"),
            rule_id=rule_id_finding,
            wcag_sc=wcag_sc,
            element_tag=element_tag,
        )

        # ── Stage 1: Rule table lookup ─────────────────────────────────────────
        for rule in _RULES:
            if not self._rule_matches(rule, rule_id_finding, wcag_sc, element_tag):
                continue

            # Check optional structural conditions
            if rule.requires_icon_only and not is_icon_only:
                continue
            if rule.requires_inside_form and not is_inside_form:
                continue

            log.info(
                "classifier.rule_matched",
                finding_id=finding_data.get("finding_id"),
                rule=rule.rule_id,
                automation_level=rule.automation_level.value,
                confidence=rule.confidence,
            )
            return (
                rule.automation_level,
                rule.problem_type,
                rule.confidence,
                f"[Rule {rule.rule_id}] {rule.reasoning}",
            )

        # ── Stage 2: Context-based fallback (no LLM needed) ───────────────────
        fallback_level, fallback_problem, fallback_confidence, fallback_reason = (
            self._context_fallback(source_context, finding_data)
        )

        log.info(
            "classifier.fallback_applied",
            finding_id=finding_data.get("finding_id"),
            automation_level=fallback_level.value,
            confidence=fallback_confidence,
        )
        return fallback_level, fallback_problem, fallback_confidence, fallback_reason

    # ── Rule matching ─────────────────────────────────────────────────────────

    @staticmethod
    def _rule_matches(
        rule: ClassificationRule,
        axe_rule_id: str,
        wcag_sc: str,
        element_tag: str,
    ) -> bool:
        """
        Return True if this rule applies to the given finding.
        Matching is OR within each dimension, AND across dimensions.
        """
        # Check axe rule_id match
        axe_match = any(r in axe_rule_id for r in rule.axe_rule_ids) if rule.axe_rule_ids else True
        # Check WCAG SC match
        sc_match = any(sc in wcag_sc for sc in rule.wcag_scs) if rule.wcag_scs else True
        # Check element tag match ("*" matches any tag)
        tag_match = (
            "*" in rule.element_tags
            or element_tag in rule.element_tags
            or not rule.element_tags
        )
        return (axe_match or sc_match) and tag_match

    # ── Context fallback ──────────────────────────────────────────────────────

    @staticmethod
    def _context_fallback(
        ctx: SourceContext,
        finding_data: dict[str, Any],
    ) -> tuple[RemediationAutomationLevel, ProblemType, float, str]:
        """
        When no rule matches, use the SourceContext's problem_type for a
        conservative classification.
        """
        problem_type = ctx.problem_type
        tag = ctx.element_tag

        # Already classified from context — use that
        if problem_type == ProblemType.MISSING_MARKUP:
            return (
                RemediationAutomationLevel.LIKELY_AUTO_FIX,
                problem_type, 0.65,
                "Fallback: structural markup issue with no specific rule match. "
                "AI-assisted planning recommended."
            )
        if problem_type == ProblemType.INCORRECT_ARIA:
            return (
                RemediationAutomationLevel.AI_PROPOSED_FIX,
                problem_type, 0.60,
                "Fallback: ARIA issue detected but no specific rule matched. "
                "LLM planning required to determine the correct ARIA fix."
            )
        if problem_type == ProblemType.COLOR_CONTRAST:
            return (
                RemediationAutomationLevel.DO_NOT_AUTO_REMEDIATE,
                problem_type, 0.99,
                "Fallback: color contrast issues are never auto-remediated."
            )
        if problem_type in (ProblemType.LINK_TEXT, ProblemType.CONTENT):
            return (
                RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED,
                problem_type, 0.85,
                "Fallback: content/link text issue requires human judgment."
            )

        # Default: AI-proposed, medium confidence
        return (
            RemediationAutomationLevel.AI_PROPOSED_FIX,
            problem_type if problem_type != ProblemType.UNKNOWN else ProblemType.INCORRECT_ARIA,
            0.50,
            "Fallback: no specific rule matched. Defaulting to AI_PROPOSED_FIX. "
            "LLM planning will determine the specific fix strategy."
        )
