"""
Finding Deduplication and Normalization Engine.

Prevents the same underlying accessibility defect from appearing multiple
times in the final report due to:
- Multiple page states exposing the same component
- Repeated DOM elements (e.g., 100 identical buttons without names)
- Multiple axe rules flagging the same root cause

Design:
    Deduplication uses a multi-level strategy:
    1. Exact match: same rule_id + selector → strict duplicate
    2. Component signature match: same component_signature hash → likely duplicate
    3. Fuzzy match: same SC + similar selector (class-based) → possible duplicate

    The first finding encountered becomes the "canonical" finding.
    Subsequent duplicates are marked with duplicate_of=<canonical_id>.

This module is DETERMINISTIC — no LLM calls.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from accessibility_agent.logging_config import get_logger
from accessibility_agent.wcag.schemas import Finding, FindingStatus

log = get_logger(__name__)


class DeduplicationEngine:
    """
    Identifies and marks duplicate findings within a scan result set.

    Modifies findings in-place by setting duplicate_of on duplicates.
    The canonical (first seen) finding is updated with the related_findings list.
    """

    def __init__(self, similarity_threshold: float = 0.85) -> None:
        self._threshold = similarity_threshold

    def deduplicate(self, findings: list[Finding]) -> list[Finding]:
        """
        Process a list of findings and mark duplicates.

        Returns the same list (modified in-place) with:
        - Duplicate findings having duplicate_of set to the canonical finding_id
        - Canonical findings having related_findings populated

        Only non-pass, non-N/A findings are deduplicated.
        """
        active = [
            f for f in findings
            if f.status not in (FindingStatus.PASS, FindingStatus.NOT_APPLICABLE)
        ]

        # ── Pass 1: Exact component signature match ────────────────────────
        canonical_by_signature: dict[str, Finding] = {}

        for finding in active:
            if not finding.component_signature:
                continue
            if finding.component_signature in canonical_by_signature:
                canonical = canonical_by_signature[finding.component_signature]
                self._mark_duplicate(finding, canonical)
            else:
                canonical_by_signature[finding.component_signature] = finding

        # ── Pass 2: Rule + normalized selector match ───────────────────────
        canonical_by_rule_selector: dict[str, Finding] = {}

        for finding in active:
            if finding.duplicate_of:
                continue  # Already marked as duplicate
            key = self._rule_selector_key(finding)
            if key in canonical_by_rule_selector:
                canonical = canonical_by_rule_selector[key]
                if canonical.finding_id != finding.finding_id:
                    self._mark_duplicate(finding, canonical)
            else:
                canonical_by_rule_selector[key] = finding

        # ── Pass 3: SC + class-based selector fuzzy match ─────────────────
        canonical_by_sc_class: dict[str, Finding] = {}

        for finding in active:
            if finding.duplicate_of:
                continue
            key = self._sc_class_key(finding)
            if key in canonical_by_sc_class:
                canonical = canonical_by_sc_class[key]
                # Only merge if selector similarity is above threshold
                if self._selector_similarity(
                    finding.element.selector, canonical.element.selector
                ) >= self._threshold:
                    self._mark_duplicate(finding, canonical)
            else:
                canonical_by_sc_class[key] = finding

        total = len(active)
        duplicates = sum(1 for f in active if f.duplicate_of)
        log.info(
            "dedup.complete",
            total_active=total,
            duplicates_marked=duplicates,
            unique=total - duplicates,
        )
        return findings

    @staticmethod
    def _mark_duplicate(finding: Finding, canonical: Finding) -> None:
        """Mark *finding* as a duplicate of *canonical* (in-place)."""
        finding.duplicate_of = canonical.finding_id
        if finding.finding_id not in canonical.related_findings:
            canonical.related_findings.append(finding.finding_id)
        log.debug(
            "dedup.duplicate_marked",
            duplicate=finding.finding_id,
            canonical=canonical.finding_id,
        )

    @staticmethod
    def _rule_selector_key(finding: Finding) -> str:
        """Key based on rule_id + normalized selector."""
        normalized = _normalize_selector(finding.element.selector)
        return f"{finding.rule_id}::{finding.wcag.success_criterion}::{normalized}"

    @staticmethod
    def _sc_class_key(finding: Finding) -> str:
        """Key based on SC + element classes (for cross-element grouping)."""
        classes = _extract_classes(finding.element.selector)
        return f"{finding.wcag.success_criterion}::{classes}"

    @staticmethod
    def _selector_similarity(sel_a: str, sel_b: str) -> float:
        """
        Simple character-level similarity metric for two CSS selectors.
        Returns a value in [0.0, 1.0].
        """
        if not sel_a or not sel_b:
            return 0.0
        if sel_a == sel_b:
            return 1.0
        # Jaccard similarity on selector token sets
        tokens_a = set(re.split(r"[\s>~+\[\]#.:]", sel_a))
        tokens_b = set(re.split(r"[\s>~+\[\]#.:]", sel_b))
        tokens_a.discard("")
        tokens_b.discard("")
        if not tokens_a and not tokens_b:
            return 1.0
        intersection = len(tokens_a & tokens_b)
        union = len(tokens_a | tokens_b)
        return intersection / union if union else 0.0


def _normalize_selector(selector: str) -> str:
    """
    Normalize a CSS selector for deduplication comparison.
    Strips nth-child indices and trailing pseudo-classes.
    """
    normalized = re.sub(r":nth-child\(\d+\)", "", selector)
    normalized = re.sub(r":\w+\(.*?\)", "", normalized)
    normalized = normalized.strip()
    return normalized


def _extract_classes(selector: str) -> str:
    """Extract class names from a CSS selector for fuzzy grouping."""
    classes = re.findall(r"\.([a-zA-Z0-9_-]+)", selector)
    return "|".join(sorted(classes))
