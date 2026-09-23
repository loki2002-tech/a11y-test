"""
WCAG Mapping Engine.

Responsible for:
1. Validating that a finding's WCAG mapping is correct and current.
2. Enriching a partial mapping (SC number only) with full metadata.
3. Normalising axe-core rule IDs to their WCAG Success Criteria.

Design principle: If a mapping cannot be validated against authoritative
data, the engine returns None rather than guessing.  The caller must then
set status=CANNOT_DETERMINE.
"""

from __future__ import annotations

import re

from accessibility_agent.logging_config import get_logger
from accessibility_agent.wcag.criteria import WCAG_22_CRITERIA, get_sc, validate_sc
from accessibility_agent.wcag.schemas import (
    WCAGLevel,
    WCAGMapping,
    WCAGPrinciple,
    WCAGVersion,
)

log = get_logger(__name__)

# ── axe-core rule → WCAG SC mapping ──────────────────────────────────────────
# Source: https://github.com/dequelabs/axe-core/blob/develop/doc/rule-descriptions.md
# This mapping is PARTIAL.  axe-core rules not listed here will be enriched
# from the axe result's tags field.
AXE_RULE_TO_SC: dict[str, list[str]] = {
    "area-alt": ["1.1.1"],
    "aria-allowed-attr": ["4.1.2"],
    "aria-allowed-role": ["4.1.2"],
    "aria-braille-equivalent": ["4.1.2"],
    "aria-command-name": ["4.1.2"],
    "aria-conditional-attr": ["4.1.2"],
    "aria-deprecated-role": ["4.1.2"],
    "aria-dialog-name": ["4.1.2"],
    "aria-hidden-body": ["4.1.2"],
    "aria-hidden-focus": ["4.1.2"],
    "aria-input-field-name": ["4.1.2"],
    "aria-meter-name": ["1.1.1", "4.1.2"],
    "aria-progressbar-name": ["1.1.1", "4.1.2"],
    "aria-prohibited-attr": ["4.1.2"],
    "aria-required-attr": ["4.1.2"],
    "aria-required-children": ["1.3.1", "4.1.2"],
    "aria-required-parent": ["1.3.1", "4.1.2"],
    "aria-roledescription": ["4.1.2"],
    "aria-roles": ["4.1.2"],
    "aria-text": ["4.1.2"],
    "aria-toggle-field-name": ["4.1.2"],
    "aria-tooltip-name": ["4.1.2"],
    "aria-treeitem-name": ["4.1.2"],
    "aria-valid-attr-value": ["4.1.2"],
    "aria-valid-attr": ["4.1.2"],
    "audio-caption": ["1.2.1", "1.2.2"],
    "autocomplete-valid": ["1.3.5"],
    "avoid-inline-spacing": ["1.4.12"],
    "blink": ["2.2.2"],
    "button-name": ["4.1.2"],
    "bypass": ["2.4.1"],
    "color-contrast": ["1.4.3"],
    "color-contrast-enhanced": ["1.4.6"],
    "css-orientation-lock": ["1.3.4"],
    "definition-list": ["1.3.1"],
    "dlitem": ["1.3.1"],
    "document-title": ["2.4.2"],
    "duplicate-id-aria": ["4.1.2"],
    "empty-heading": ["2.4.6"],
    "empty-table-header": ["1.3.1"],
    "focus-trap": ["2.1.2"],
    "focusable-disabled": ["2.1.1"],
    "focusable-modal-open": ["2.1.2"],
    "focusable-no-name": ["4.1.2"],
    "form-field-multiple-labels": ["1.3.1", "3.3.2"],
    "frame-focusable-content": ["2.1.1"],
    "frame-tested": ["4.1.2"],
    "frame-title": ["4.1.2"],
    "heading-order": ["1.3.1"],
    "hidden-content": [],
    "html-has-lang": ["3.1.1"],
    "html-lang-valid": ["3.1.1"],
    "html-xml-lang-mismatch": ["3.1.1"],
    "identical-links-same-purpose": ["2.4.9"],
    "image-alt": ["1.1.1"],
    "image-redundant-alt": ["1.1.1"],
    "input-button-name": ["4.1.2"],
    "input-image-alt": ["1.1.1"],
    "label": ["1.3.1", "3.3.2"],
    "label-content-name-mismatch": ["2.5.3"],
    "landmark-banner-is-top-level": ["1.3.1"],
    "landmark-complementary-is-top-level": ["1.3.1"],
    "landmark-contentinfo-is-top-level": ["1.3.1"],
    "landmark-main-is-top-level": ["1.3.1"],
    "landmark-no-duplicate-banner": ["1.3.1"],
    "landmark-no-duplicate-contentinfo": ["1.3.1"],
    "landmark-no-duplicate-main": ["1.3.1"],
    "landmark-one-main": ["1.3.1"],
    "landmark-unique": ["1.3.1"],
    "link-in-text-block": ["1.4.1"],
    "link-name": ["2.4.4", "4.1.2"],
    "list": ["1.3.1"],
    "listitem": ["1.3.1"],
    "marquee": ["2.2.2"],
    "meta-refresh": ["2.2.1"],
    "meta-viewport": ["1.4.4"],
    "nested-interactive": ["4.1.2"],
    "no-autoplay-audio": ["1.4.2"],
    "object-alt": ["1.1.1"],
    "region": ["1.3.1"],
    "role-img-alt": ["1.1.1"],
    "scope-attr-valid": ["1.3.1"],
    "scrollable-region-focusable": ["2.1.1"],
    "select-name": ["1.3.1", "3.3.2", "4.1.2"],
    "server-side-image-map": ["2.1.1"],
    "skip-link": ["2.4.1"],
    "summary-name": ["1.3.1"],
    "svg-img-alt": ["1.1.1"],
    "tabindex": ["2.4.3"],
    "table-duplicate-name": ["1.3.1"],
    "table-fake-caption": ["1.3.1"],
    "target-size": ["2.5.8"],
    "td-headers-attr": ["1.3.1"],
    "th-has-data-cells": ["1.3.1"],
    "valid-lang": ["3.1.2"],
    "video-caption": ["1.2.2"],
}


class WCAGMapper:
    """
    Validates and enriches WCAG mappings.

    Every finding produced by the system must pass through the mapper to ensure:
    - The SC number is valid.
    - The title, level, and principle match the authoritative reference.
    - The mapping is not invented by the AI.
    """

    def enrich_from_sc(self, sc_number: str) -> WCAGMapping | None:
        """
        Given a valid SC number (e.g. '1.1.1'), return a fully populated
        WCAGMapping using the authoritative criteria reference.

        Returns None if the SC is not found, preventing hallucinated mappings.
        """
        entry = get_sc(sc_number)
        if entry is None:
            log.warning("wcag_mapper.unknown_sc", sc=sc_number)
            return None

        return WCAGMapping(
            version=WCAGVersion.V22,
            success_criterion=sc_number,
            title=entry["title"],
            level=WCAGLevel(entry["level"]),
            principle=WCAGPrinciple(entry["principle"]),
            url=entry["url"],
        )

    def enrich_from_axe_rule(self, rule_id: str) -> list[WCAGMapping]:
        """
        Return a list of WCAGMappings for the given axe-core rule ID.

        Uses the AXE_RULE_TO_SC lookup table.  Falls back to an empty list
        if the rule is not mapped, rather than guessing.
        """
        sc_list = AXE_RULE_TO_SC.get(rule_id, [])
        mappings: list[WCAGMapping] = []
        for sc in sc_list:
            m = self.enrich_from_sc(sc)
            if m:
                mappings.append(m)

        if not mappings:
            log.debug("wcag_mapper.unmapped_axe_rule", rule_id=rule_id)

        return mappings

    def validate_mapping(self, mapping: WCAGMapping) -> bool:
        """
        Validate that an existing WCAGMapping is internally consistent with
        the authoritative reference data.  Returns False if any field
        contradicts the specification.
        """
        entry = get_sc(mapping.success_criterion)
        if entry is None:
            log.warning("wcag_validator.invalid_sc", sc=mapping.success_criterion)
            return False

        if mapping.title != entry["title"]:
            log.warning(
                "wcag_validator.title_mismatch",
                sc=mapping.success_criterion,
                provided=mapping.title,
                expected=entry["title"],
            )
            return False

        if mapping.level.value != entry["level"]:
            log.warning(
                "wcag_validator.level_mismatch",
                sc=mapping.success_criterion,
                provided=mapping.level.value,
                expected=entry["level"],
            )
            return False

        return True

    def extract_sc_from_axe_tags(self, tags: list[str]) -> list[str]:
        """
        Extract Success Criterion numbers from axe-core result tags.
        axe-core encodes SCs in tags like 'wcag111', 'wcag2aa', etc.

        Example: ['wcag2a', 'wcag111'] → ['1.1.1']
        """
        sc_numbers: list[str] = []
        for tag in tags:
            # Match patterns like wcag111, wcag1411, wcag243
            match = re.fullmatch(r"wcag(\d{3,4})", tag)
            if match:
                digits = match.group(1)
                # Convert axe tag digits to SC format: '111' → '1.1.1'
                formatted = self._digits_to_sc(digits)
                if formatted and validate_sc(formatted):
                    sc_numbers.append(formatted)
        return list(dict.fromkeys(sc_numbers))  # deduplicate, preserve order

    @staticmethod
    def _digits_to_sc(digits: str) -> str | None:
        """Convert axe digit string to SC format."""
        # 3-digit: e.g. '111' → '1.1.1', '243' → '2.4.3'
        # 4-digit: e.g. '1411' → '1.4.11', '1412' → '1.4.12'
        if len(digits) == 3:
            return f"{digits[0]}.{digits[1]}.{digits[2]}"
        if len(digits) == 4:
            # Could be x.y.zz (most cases) or x.yy.z
            # WCAG principles are 1-4, guidelines are 1-5, SC are 1-13
            # Heuristic: first digit is principle (1-4), second is guideline
            p = digits[0]
            g = digits[1]
            sc = digits[2:]
            candidate = f"{p}.{g}.{sc}"
            if validate_sc(candidate):
                return candidate
            # Try x.yy form: e.g. '1411' could also be 1.4.11
            g2 = digits[1:3]
            sc2 = digits[3]
            candidate2 = f"{p}.{g2}.{sc2}"
            if validate_sc(candidate2):
                return candidate2
        return None


# Module-level singleton
wcag_mapper = WCAGMapper()
