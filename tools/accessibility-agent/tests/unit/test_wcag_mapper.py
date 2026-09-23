"""
Unit tests for the WCAG Mapper.

Tests verify:
- Valid SC enrichment
- Unknown SC returns None (not a hallucinated mapping)
- axe-core rule → SC mapping correctness
- axe tag parsing (e.g. wcag111 → 1.1.1)
- Mapping validation against authoritative data
"""

from __future__ import annotations

import pytest

from accessibility_agent.wcag.mapper import WCAGMapper, wcag_mapper
from accessibility_agent.wcag.schemas import WCAGLevel, WCAGPrinciple


class TestWCAGMapper:
    def setup_method(self):
        self.mapper = WCAGMapper()

    def test_enrich_known_sc(self):
        m = self.mapper.enrich_from_sc("1.1.1")
        assert m is not None
        assert m.title == "Non-text Content"
        assert m.level == WCAGLevel.A
        assert m.principle == WCAGPrinciple.PERCEIVABLE
        assert "wcag22" in m.url.lower()

    def test_enrich_unknown_sc_returns_none(self):
        """Unknown SCs must return None, not a hallucinated mapping."""
        result = self.mapper.enrich_from_sc("9.9.9")
        assert result is None

    def test_enrich_sc_1412(self):
        """Test a 4-digit SC (1.4.12 Text Spacing)."""
        m = self.mapper.enrich_from_sc("1.4.12")
        assert m is not None
        assert m.title == "Text Spacing"
        assert m.level == WCAGLevel.AA

    def test_enrich_from_axe_rule_image_alt(self):
        mappings = self.mapper.enrich_from_axe_rule("image-alt")
        assert len(mappings) >= 1
        scs = [m.success_criterion for m in mappings]
        assert "1.1.1" in scs

    def test_enrich_from_axe_rule_color_contrast(self):
        mappings = self.mapper.enrich_from_axe_rule("color-contrast")
        scs = [m.success_criterion for m in mappings]
        assert "1.4.3" in scs

    def test_enrich_from_axe_rule_unknown_returns_empty(self):
        """Unknown axe rules return empty list, not an error."""
        mappings = self.mapper.enrich_from_axe_rule("made-up-rule-xyz")
        assert mappings == []

    def test_extract_sc_from_axe_tags_111(self):
        """wcag111 tag → '1.1.1'"""
        result = self.mapper.extract_sc_from_axe_tags(["wcag2a", "wcag111", "best-practice"])
        assert "1.1.1" in result

    def test_extract_sc_from_axe_tags_1412(self):
        """wcag1412 tag → '1.4.12'"""
        result = self.mapper.extract_sc_from_axe_tags(["wcag1412"])
        assert "1.4.12" in result

    def test_extract_sc_from_axe_tags_no_match(self):
        """Non-SC tags produce empty list."""
        result = self.mapper.extract_sc_from_axe_tags(["wcag2aa", "best-practice", "cat.color"])
        assert result == []

    def test_validate_mapping_correct(self):
        m = self.mapper.enrich_from_sc("2.4.7")
        assert m is not None
        assert self.mapper.validate_mapping(m) is True

    def test_validate_mapping_wrong_title_fails(self):
        m = self.mapper.enrich_from_sc("1.1.1")
        assert m is not None
        m.title = "Wrong Title — Hallucinated"
        assert self.mapper.validate_mapping(m) is False

    def test_validate_mapping_wrong_level_fails(self):
        m = self.mapper.enrich_from_sc("1.4.3")  # Should be AA
        assert m is not None
        m.level = WCAGLevel.A  # Intentionally wrong
        assert self.mapper.validate_mapping(m) is False

    def test_module_singleton_works(self):
        """The module-level singleton is pre-initialised."""
        m = wcag_mapper.enrich_from_sc("4.1.2")
        assert m is not None
        assert m.title == "Name, Role, Value"
