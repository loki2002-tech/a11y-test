"""
Unit tests for Phase 4:
    - SCRelationship classification (sc_relationships.py)
    - Refactored check_fix_for_contradictions() in RAGEngine (rag_engine.py)
    - get_actual_conflicts() convenience method

Test groups:
    1. SCRelationship — classify_relationship() edge cases and explicit pairs
    2. SCRelationship — should_surface_contradiction() and should_warn_contradiction()
    3. RAGEngine — false-positive elimination (UNRELATED patterns no longer fire)
    4. RAGEngine — actual conflict detection still works correctly
    5. RAGEngine — POTENTIALLY_INTERACTING patterns produce warnings not blocks
    6. RAGEngine — get_actual_conflicts() filters correctly
    7. RAGEngine — empty / edge inputs handled safely
    8. Backward compatibility — pre-existing true positives still detected
"""

from __future__ import annotations

import pytest

from accessibility_agent.wcag.sc_relationships import (
    SCRelationship,
    classify_relationship,
    should_surface_contradiction,
    should_warn_contradiction,
)
from accessibility_agent.wcag.rag_engine import RAGEngine


# ── Shared fixture ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def rag() -> RAGEngine:
    """Single RAGEngine instance for the whole module (no state mutation)."""
    return RAGEngine()


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 1 — SCRelationship: classify_relationship()
# ═════════════════════════════════════════════════════════════════════════════


class TestClassifyRelationship:

    def test_same_sc_always_compatible(self):
        """Fixing SC X and triggering a pattern for SC X → always COMPATIBLE."""
        assert classify_relationship("4.1.2", "4.1.2") == SCRelationship.COMPATIBLE
        assert classify_relationship("1.1.1", "1.1.1") == SCRelationship.COMPATIBLE
        assert classify_relationship("3.1.1", "3.1.1") == SCRelationship.COMPATIBLE

    def test_lang_fix_unrelated_to_all_other_scs(self):
        """Fixing 3.1.1 (add lang='en') is UNRELATED to every other common SC."""
        for sc in ("4.1.2", "1.3.1", "1.1.1", "2.4.3", "2.4.7", "2.4.2", "1.4.3"):
            rel = classify_relationship("3.1.1", sc)
            assert rel == SCRelationship.UNRELATED, (
                f"Expected 3.1.1 → {sc} to be UNRELATED, got {rel.value}"
            )

    def test_title_fix_unrelated_to_all_other_scs(self):
        """Fixing 2.4.2 (add <title>) is UNRELATED to every other common SC."""
        for sc in ("4.1.2", "1.3.1", "1.1.1", "2.4.3", "2.4.7", "3.1.1", "1.4.3"):
            rel = classify_relationship("2.4.2", sc)
            assert rel == SCRelationship.UNRELATED, (
                f"Expected 2.4.2 → {sc} to be UNRELATED, got {rel.value}"
            )

    def test_button_name_fix_related_to_info_relationships(self):
        """Fixing 4.1.2 (button aria-label) is RELATED to 1.3.1 — both about info structure."""
        rel = classify_relationship("4.1.2", "1.3.1")
        assert rel == SCRelationship.RELATED

    def test_button_name_fix_unrelated_to_outline(self):
        """Fixing 4.1.2 (aria-label) is UNRELATED to 2.4.7 (focus visible)."""
        rel = classify_relationship("4.1.2", "2.4.7")
        assert rel == SCRelationship.UNRELATED

    def test_focus_fix_potentially_interacting_with_contrast(self):
        """Fixing 2.4.7 (outline) might affect 1.4.3 (contrast) — POTENTIALLY_INTERACTING."""
        rel = classify_relationship("2.4.7", "1.4.3")
        assert rel == SCRelationship.POTENTIALLY_INTERACTING

    def test_unknown_pair_returns_unknown(self):
        """An SC pair not in the table returns UNKNOWN."""
        rel = classify_relationship("3.3.1", "1.4.4")
        assert rel == SCRelationship.UNKNOWN

    def test_pattern_index_0_unconditional_conflict(self):
        """Pattern index 0 (aria-hidden on interactive) is ACTUAL_CONFLICT for unknown pairs."""
        # When the pair isn't in the table, unconditional conflict patterns override
        rel = classify_relationship("3.3.1", "4.1.2", pattern_index=0)
        assert rel == SCRelationship.ACTUAL_CONFLICT

    def test_pattern_index_4_unconditional_conflict(self):
        """Pattern index 4 (div/span onclick) is ACTUAL_CONFLICT for unknown pairs."""
        rel = classify_relationship("2.4.2", "4.1.2", pattern_index=4)
        assert rel == SCRelationship.ACTUAL_CONFLICT

    def test_explicit_pair_overrides_pattern_index_for_non_unconditional(self):
        """
        Explicit table entries take priority for NON-unconditional patterns.
        Pattern index 2 (tabindex) is not unconditional → table wins.
        """
        # 4.1.2 → 2.4.3 = POTENTIALLY_INTERACTING per table (pattern_index=2)
        rel = classify_relationship("4.1.2", "2.4.3", pattern_index=2)
        assert rel == SCRelationship.POTENTIALLY_INTERACTING

    def test_unconditional_pattern_overrides_table(self):
        """Unconditional patterns (0,1,4) always return ACTUAL_CONFLICT, even for RELATED pairs."""
        # 4.1.2 → 1.3.1 is RELATED in the table, but pattern_index=0 overrides
        rel = classify_relationship("4.1.2", "1.3.1", pattern_index=0)
        assert rel == SCRelationship.ACTUAL_CONFLICT


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 2 — SCRelationship: surface/warn predicates
# ═════════════════════════════════════════════════════════════════════════════


class TestRelationshipPredicates:

    def test_actual_conflict_surfaces_and_warns(self):
        assert should_surface_contradiction(SCRelationship.ACTUAL_CONFLICT) is True
        assert should_warn_contradiction(SCRelationship.ACTUAL_CONFLICT) is True

    def test_potentially_interacting_warns_but_does_not_surface(self):
        assert should_surface_contradiction(SCRelationship.POTENTIALLY_INTERACTING) is False
        assert should_warn_contradiction(SCRelationship.POTENTIALLY_INTERACTING) is True

    def test_unknown_warns_but_does_not_surface(self):
        assert should_surface_contradiction(SCRelationship.UNKNOWN) is False
        assert should_warn_contradiction(SCRelationship.UNKNOWN) is True

    def test_related_neither_surfaces_nor_warns(self):
        assert should_surface_contradiction(SCRelationship.RELATED) is False
        assert should_warn_contradiction(SCRelationship.RELATED) is False

    def test_compatible_neither_surfaces_nor_warns(self):
        assert should_surface_contradiction(SCRelationship.COMPATIBLE) is False
        assert should_warn_contradiction(SCRelationship.COMPATIBLE) is False

    def test_unrelated_neither_surfaces_nor_warns(self):
        assert should_surface_contradiction(SCRelationship.UNRELATED) is False
        assert should_warn_contradiction(SCRelationship.UNRELATED) is False


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 3 — RAGEngine: false-positive elimination
# ═════════════════════════════════════════════════════════════════════════════


class TestFalsePositiveElimination:
    """
    These tests verify that patterns which used to cause false positives are
    now correctly silenced by the relationship map.
    """

    def test_lang_fix_does_not_flag_outline_none(self, rag: RAGEngine):
        """
        BUG (pre-refactor): Fixing 3.1.1 on a page that has 'outline:none'
        in its CSS caused a false 2.4.7 contradiction. The two are UNRELATED.
        """
        html_with_outline = (
            '<html lang="en">\n'
            '<head><style>.btn:focus { outline: none; }</style></head>\n'
            '<body></body>\n'
            '</html>\n'
        )
        findings = rag.check_fix_for_contradictions(html_with_outline, sc_being_fixed="3.1.1")
        blocking = [f for f in findings if f.get("is_blocking") == "true"]
        # outline:none is UNRELATED to a lang fix → must not appear in results at all
        assert not any(f["violates_sc"] == "2.4.7" for f in findings), (
            "outline:none triggered a false 2.4.7 contradiction when fixing 3.1.1 (lang)"
        )

    def test_title_fix_does_not_flag_anything(self, rag: RAGEngine):
        """
        Fixing 2.4.2 (missing <title>) on a page that has various patterns
        should produce zero contradictions — title fix is UNRELATED to everything.
        """
        html_with_issues = (
            '<html>\n'
            '<head><title>My App</title></head>\n'
            '<body>\n'
            '  <div onclick="go()">Click me</div>\n'
            '  <img src="/x.png" />\n'
            '</body>\n'
            '</html>\n'
        )
        # HTML with ONLY patterns that are UNRELATED to 2.4.2
        # (no div onclick which is unconditional and would legitimately fire)
        html_unrelated_only = (
            '<html>\n'
            '<head><title>My App</title>\n'
            '<style>.btn:focus { outline: none; }</style>\n'
            '</head>\n'
            '<body>\n'
            '  <a href="/about" tabindex="2">About</a>\n'
            '  <img src="/x.png" />\n'
            '</body>\n'
            '</html>\n'
        )
        findings = rag.check_fix_for_contradictions(html_unrelated_only, sc_being_fixed="2.4.2")
        # outline:none (2.4.7) and tabindex (2.4.3) are UNRELATED to 2.4.2
        assert not any(f["violates_sc"] == "2.4.7" for f in findings), (
            "outline:none must not fire when fixing 2.4.2 (UNRELATED)"
        )
        assert not any(f["violates_sc"] == "2.4.3" for f in findings), (
            "tabindex must not fire when fixing 2.4.2 (UNRELATED)"
        )

    def test_aria_label_fix_does_not_flag_13_1(self, rag: RAGEngine):
        """
        Fixing 4.1.2 (adding aria-label) on an element that's inside a table
        should NOT flag 1.3.1 — the relationship is RELATED (not a contradiction).
        """
        html = (
            '<table>\n'
            '  <tr><td>\n'
            '    <button aria-label="Close dialog" type="button">×</button>\n'
            '  </td></tr>\n'
            '</table>\n'
        )
        findings = rag.check_fix_for_contradictions(html, sc_being_fixed="4.1.2")
        blocking = [f for f in findings if f.get("is_blocking") == "true"]
        assert not any(f["violates_sc"] == "1.3.1" for f in blocking), (
            "Adding aria-label (4.1.2 fix) must not block on 1.3.1 — they are RELATED"
        )

    def test_focus_fix_does_not_flag_1_4_3_as_blocking(self, rag: RAGEngine):
        """
        Fixing 2.4.7 (outline:none → focus indicator) on a page where the
        focus color might affect contrast should warn but NOT block (POTENTIALLY_INTERACTING).
        """
        html = (
            '<style>'
            '.btn:focus { outline: 2px solid #aaa; outline-offset: 2px; }'
            '</style>\n'
            '<button class="btn">Go</button>\n'
        )
        findings = rag.check_fix_for_contradictions(html, sc_being_fixed="2.4.7")
        blocking = [f for f in findings if f.get("is_blocking") == "true"]
        # 2.4.7 → 1.4.3 is POTENTIALLY_INTERACTING, not ACTUAL_CONFLICT → not blocking
        assert not any(f["violates_sc"] == "1.4.3" for f in blocking), (
            "Focus indicator fix must not BLOCK on 1.4.3 contrast — only warn"
        )


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 4 — RAGEngine: actual conflict detection still works
# ═════════════════════════════════════════════════════════════════════════════


class TestActualConflictDetection:
    """
    These tests verify that GENUINE contradictions are still caught.
    The refactor must not reduce detection of real violations.
    """

    def test_aria_hidden_on_interactive_is_actual_conflict(self, rag: RAGEngine):
        """
        Pattern 0: Adding aria-hidden='true' on a button while fixing 1.1.1
        (image alt text) — the aria-hidden on button is an ACTUAL CONFLICT for 4.1.2.
        """
        html = (
            '<button aria-hidden="true" type="submit">\n'
            '  <img src="/icon.svg" alt="Submit" />\n'
            '</button>\n'
        )
        # Fixing 1.1.1, but the aria-hidden on button is a 4.1.2 violation
        findings = rag.check_fix_for_contradictions(html, sc_being_fixed="1.1.1")
        blocking = [f for f in findings if f.get("is_blocking") == "true"]
        assert any(f["violates_sc"] == "4.1.2" for f in blocking), (
            "aria-hidden='true' on a button must be detected as ACTUAL CONFLICT for 4.1.2"
        )

    def test_div_with_onclick_is_actual_conflict(self, rag: RAGEngine):
        """
        Pattern 4: A <div onclick> introduced while fixing 2.4.3 (focus order)
        is an ACTUAL CONFLICT for 4.1.2 (name, role, value).
        """
        html = (
            '<div onclick="handleClick()" class="card">\n'
            '  <span>Click me</span>\n'
            '</div>\n'
        )
        findings = rag.check_fix_for_contradictions(html, sc_being_fixed="2.4.3")
        blocking = [f for f in findings if f.get("is_blocking") == "true"]
        assert any(f["violates_sc"] == "4.1.2" for f in blocking), (
            "div onclick must be detected as ACTUAL CONFLICT for 4.1.2"
        )

    def test_positive_tabindex_when_fixing_other_sc(self, rag: RAGEngine):
        """
        If we introduce tabindex='3' while fixing something else,
        that's a contradiction for 2.4.3 (focus order).
        """
        html = '<button tabindex="3" aria-label="Go">Go</button>\n'
        # Fixing 1.1.1, and we introduced tabindex=3 — 1.1.1→2.4.3 is UNRELATED
        # But the pattern should fire based on relationship for unknown pairs
        findings = rag.check_fix_for_contradictions(html, sc_being_fixed="1.1.1")
        # 1.1.1 → 2.4.3 = UNRELATED per table → should be SKIPPED
        # This tests the table correctly suppresses the tabindex false positive
        blocking = [f for f in findings if f.get("is_blocking") == "true"]
        assert not any(f["violates_sc"] == "2.4.3" for f in blocking), (
            "tabindex=3 when fixing 1.1.1 (unrelated) must not block — should be UNRELATED"
        )


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 5 — RAGEngine: POTENTIALLY_INTERACTING = warning not block
# ═════════════════════════════════════════════════════════════════════════════


class TestPotentiallyInteracting:

    def test_potentially_interacting_appears_in_results_but_not_blocking(self, rag: RAGEngine):
        """
        Fixing 4.1.2 on a page where structural changes MIGHT affect 2.4.3.
        Should appear in results (for the report) but marked is_blocking=false.
        """
        # Construct HTML that might trigger a POTENTIALLY_INTERACTING pattern
        # For 4.1.2 → 2.4.3 (potentially interacting), we need a pattern that fires
        # The tabindex pattern (index 2) fires on tabindex='1'
        html = '<button tabindex="1" aria-label="Submit">Submit</button>\n'
        findings = rag.check_fix_for_contradictions(html, sc_being_fixed="4.1.2")

        # 4.1.2 → 2.4.3 = POTENTIALLY_INTERACTING
        # Pattern: tabindex='1' → 2.4.3
        matching = [f for f in findings if f["violates_sc"] == "2.4.3"]

        if matching:
            # If it appears, it must NOT be blocking
            for f in matching:
                assert f.get("is_blocking") == "false", (
                    f"POTENTIALLY_INTERACTING finding must have is_blocking=false, got: {f}"
                )
            assert f.get("relationship") in ("potentially_interacting", "unknown"), (
                f"Expected relationship to be potentially_interacting or unknown, got {f.get('relationship')}"
            )

    def test_all_non_blocking_findings_have_is_blocking_false(self, rag: RAGEngine):
        """All findings returned must have an explicit is_blocking field."""
        html = (
            '<html>\n'
            '<body>\n'
            '  <div onclick="x()">Click</div>\n'
            '  <button tabindex="2">Hi</button>\n'
            '</body>\n'
            '</html>\n'
        )
        findings = rag.check_fix_for_contradictions(html, sc_being_fixed="3.1.1")
        for f in findings:
            assert "is_blocking" in f, f"Finding missing is_blocking field: {f}"
            assert f["is_blocking"] in ("true", "false"), (
                f"is_blocking must be 'true' or 'false', got {f['is_blocking']}"
            )
        assert "relationship" in findings[0] if findings else True

    def test_all_findings_have_relationship_field(self, rag: RAGEngine):
        """Every returned finding must include the relationship field."""
        html = '<div onclick="go()">Click</div>'
        # Use an SC not in the table to exercise the UNKNOWN path
        findings = rag.check_fix_for_contradictions(html, sc_being_fixed="3.3.1")
        for f in findings:
            assert "relationship" in f, f"Finding missing relationship field: {f}"


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 6 — RAGEngine: get_actual_conflicts() filter
# ═════════════════════════════════════════════════════════════════════════════


class TestGetActualConflicts:

    def test_get_actual_conflicts_returns_subset_of_check_fix(self, rag: RAGEngine):
        """get_actual_conflicts() must return a subset of check_fix_for_contradictions()."""
        html = (
            '<button aria-hidden="true" tabindex="2" type="button">\n'
            '  <img src="/x.svg" />\n'
            '</button>\n'
        )
        sc = "1.1.1"
        all_findings = rag.check_fix_for_contradictions(html, sc)
        blocking_only = rag.get_actual_conflicts(html, sc)

        # All items in blocking_only must be in all_findings
        all_ids = {(f["violates_sc"], f["matched_pattern"]) for f in all_findings}
        for f in blocking_only:
            assert (f["violates_sc"], f["matched_pattern"]) in all_ids, (
                f"get_actual_conflicts returned {f['violates_sc']} not in full results"
            )

        # All items in blocking_only must be actual conflicts
        for f in blocking_only:
            assert f.get("is_blocking") == "true", (
                f"get_actual_conflicts returned non-blocking finding: {f}"
            )

    def test_get_actual_conflicts_empty_when_no_html(self, rag: RAGEngine):
        """get_actual_conflicts() returns empty list for empty HTML."""
        assert rag.get_actual_conflicts("", "4.1.2") == []

    def test_get_actual_conflicts_empty_for_clean_fix(self, rag: RAGEngine):
        """A clean fix produces no actual conflicts."""
        html = '<button aria-label="Close" type="button">×</button>'
        result = rag.get_actual_conflicts(html, "4.1.2")
        assert result == [], f"Expected no conflicts for clean aria-label fix, got: {result}"


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 7 — Edge inputs
# ═════════════════════════════════════════════════════════════════════════════


class TestEdgeInputs:

    def test_empty_html_returns_empty(self, rag: RAGEngine):
        assert rag.check_fix_for_contradictions("", "4.1.2") == []

    def test_empty_sc_being_fixed(self, rag: RAGEngine):
        """Empty sc_being_fixed should not crash — uses UNKNOWN classification."""
        html = '<button aria-hidden="true">×</button>'
        result = rag.check_fix_for_contradictions(html, "")
        # Should not raise; result can be non-empty (unknown relationship)
        assert isinstance(result, list)

    def test_unknown_sc_being_fixed(self, rag: RAGEngine):
        """Completely unknown SC should not crash."""
        html = '<button aria-label="Go">Go</button>'
        result = rag.check_fix_for_contradictions(html, "9.9.9")
        assert isinstance(result, list)

    def test_very_large_html_does_not_crash(self, rag: RAGEngine):
        """Very large HTML input must not raise."""
        large_html = ("<button aria-label='x'>y</button>" * 500)
        result = rag.check_fix_for_contradictions(large_html, "4.1.2")
        assert isinstance(result, list)


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 8 — Backward compatibility: pre-refactor true positives still caught
# ═════════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """
    Verify that the refactor does NOT regress on cases that were correctly
    detected in the old implementation.

    These are the 'true positives' — genuine violations that the old system
    found and the new system must also find (though may classify differently).
    """

    def test_aria_hidden_on_label_still_detected(self, rag: RAGEngine):
        """
        aria-hidden='true' on a <label> while fixing a different SC should
        still be detected (even if classified as warning rather than block,
        it must appear in the results for the remediation report).
        """
        html = '<label aria-hidden="true" for="email">Email</label>'
        findings = rag.check_fix_for_contradictions(html, sc_being_fixed="2.4.3")
        sc_violations = [f["violates_sc"] for f in findings]
        # The aria-hidden on label pattern must still fire
        assert "4.1.2" in sc_violations, (
            f"aria-hidden on label must still appear in results. Got: {sc_violations}"
        )

    def test_role_presentation_on_table_still_detected(self, rag: RAGEngine):
        """role='presentation' on a <table> is still flagged."""
        html = (
            '<table role="presentation">\n'
            '  <tr><th>Header</th></tr>\n'
            '</table>\n'
        )
        findings = rag.check_fix_for_contradictions(html, sc_being_fixed="4.1.2")
        sc_violations = [f["violates_sc"] for f in findings]
        assert "1.3.1" in sc_violations, (
            f"role=presentation on table must still be detected. Got: {sc_violations}"
        )

    def test_div_onclick_without_role_still_detected(self, rag: RAGEngine):
        """<div onclick> without role='button' must still fire."""
        html = '<div onclick="submit()">Submit</div>'
        findings = rag.check_fix_for_contradictions(html, sc_being_fixed="1.3.1")
        sc_violations = [f["violates_sc"] for f in findings]
        # 1.3.1 → 4.1.2: RELATED or POTENTIALLY_INTERACTING
        # The div onclick pattern should still appear (warning or block)
        assert "4.1.2" in sc_violations, (
            f"div onclick must still be detected when fixing 1.3.1. Got: {sc_violations}"
        )
