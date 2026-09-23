"""
SC Relationship Map — defines which WCAG Success Criteria genuinely interact.

This module answers one question for the contradiction detector:
    "If we are fixing SC X and the proposed HTML triggers a pattern
     associated with SC Y — is that an ACTUAL CONFLICT or just noise?"

DESIGN RATIONALE
----------------
The current contradiction checker fires on ANY SC pattern match that is
not the SC being fixed. This produces massive false-positive rates:

    Example: Fixing html-has-lang (SC 3.1.1) on a page that already has
    outline:none in its CSS → checker reports SC 2.4.7 contradiction.
    But we didn't INTRODUCE outline:none. It was pre-existing.

    Example: Fixing button-name (SC 4.1.2) with aria-label → checker
    sees the aria-label attribute and flags SC 1.3.1 "Info & Relationships"
    as a potential violation. But aria-label is RECOMMENDED for 1.3.1.

The fix is to classify the relationship between the SC being fixed and
the SC that the contradiction pattern belongs to:

    ACTUAL_CONFLICT         → The fix for X DIRECTLY introduces a Y violation.
                              Surface to user. Block patch if not resolved.
    POTENTIALLY_INTERACTING → The fix for X MIGHT affect Y depending on context.
                              Log as a warning. Do NOT block. Include in report.
    RELATED                 → X and Y address the same user problem area.
                              Informational only. Do NOT report as contradiction.
    COMPATIBLE              → The fix for X is always safe for Y. Never report.
    UNRELATED               → X and Y have no meaningful interaction.
                              Never report. Pure noise.
    UNKNOWN                 → No data; use POTENTIALLY_INTERACTING as fallback.

RELATIONSHIP TABLE
------------------
Keyed by (sc_being_fixed, sc_of_contradiction_pattern) → SCRelationship.

The table is NOT symmetric. Fixing 4.1.2 with aria-label might RELATE
to 1.3.1, but fixing 1.3.1 with proper table headers does NOT affect 4.1.2.
"""

from __future__ import annotations

from enum import Enum


class SCRelationship(str, Enum):
    """
    Classifies the relationship between the SC being fixed and the SC
    that a contradiction pattern belongs to.
    """

    ACTUAL_CONFLICT = "actual_conflict"
    """
    The proposed fix for SC_A DIRECTLY introduces a SC_B violation.
    This must surface to the user and block the patch unless resolved.

    Example: Fixing image-alt (1.1.1) by adding alt="" on a functional image
             while that image also has role="img" → violates 1.1.1 differently.
    """

    POTENTIALLY_INTERACTING = "potentially_interacting"
    """
    The fix for SC_A MIGHT affect SC_B depending on element context.
    Log as a warning. Include in remediation report. Do NOT block.

    Example: Fixing 4.1.2 button-name might interact with 1.3.1
             if the button is inside a labeled group.
    """

    RELATED = "related"
    """
    SC_A and SC_B address the same or closely overlapping user problem.
    The fix for A is typically beneficial for B too.
    Informational only — never report as contradiction.

    Example: Fixing 4.1.2 Name/Role/Value is directly related to 1.3.1
             Info and Relationships. An aria-label fix helps both.
    """

    COMPATIBLE = "compatible"
    """
    The fix for SC_A is always safe for SC_B.
    The pattern match for B in the fix output is a false positive.
    Never report.

    Example: Adding lang="en" to <html> (3.1.1) is always compatible
             with every other SC. No possible conflict.
    """

    UNRELATED = "unrelated"
    """
    SC_A and SC_B have no meaningful interaction.
    The contradiction pattern match is noise. Never report.

    Example: Fixing html-has-lang (3.1.1) while the HTML has a table
             structure issue (1.3.1) — the lang fix doesn't affect tables.
    """

    UNKNOWN = "unknown"
    """
    No explicit relationship data. Use POTENTIALLY_INTERACTING as fallback.
    """


# ── Relationship Table ────────────────────────────────────────────────────────
#
# Key: (sc_being_fixed, sc_of_contradiction_pattern)
# Value: SCRelationship
#
# Rules for the table:
#   - If a pair is NOT in this table → UNKNOWN (treated as POTENTIALLY_INTERACTING)
#   - ACTUAL_CONFLICT pairs are rare and very specific
#   - When in doubt, use POTENTIALLY_INTERACTING (surfaces as warning, not block)
#
# SC shorthand used below:
#   1.1.1 = Non-text Content (alt text)
#   1.3.1 = Info and Relationships (semantic structure)
#   1.3.2 = Meaningful Sequence
#   1.4.3 = Contrast (Minimum)
#   1.4.4 = Resize Text
#   1.4.11 = Non-text Contrast
#   2.1.1 = Keyboard
#   2.4.2 = Page Titled
#   2.4.3 = Focus Order
#   2.4.4 = Link Purpose
#   2.4.7 = Focus Visible
#   3.1.1 = Language of Page
#   3.3.1 = Error Identification
#   4.1.2 = Name, Role, Value
#   4.1.3 = Status Messages

SC_RELATIONSHIPS: dict[tuple[str, str], SCRelationship] = {

    # ── Fixing 1.1.1 (Image alt text) ────────────────────────────────────────
    # Adding alt text to an image
    ("1.1.1", "4.1.2"): SCRelationship.POTENTIALLY_INTERACTING,
    # Adding alt="" (decorative) to a functional image → ACTUAL CONFLICT
    ("1.1.1", "1.1.1"): SCRelationship.COMPATIBLE,   # Skip — same SC
    ("1.1.1", "1.3.1"): SCRelationship.RELATED,       # Both about info structure
    ("1.1.1", "2.4.4"): SCRelationship.RELATED,       # Alt text is link purpose context
    ("1.1.1", "1.4.3"): SCRelationship.UNRELATED,     # Contrast unrelated to alt
    ("1.1.1", "2.4.7"): SCRelationship.UNRELATED,     # Focus unrelated to alt
    ("1.1.1", "3.1.1"): SCRelationship.UNRELATED,     # Language unrelated to alt
    ("1.1.1", "2.4.3"): SCRelationship.UNRELATED,

    # ── Fixing 1.3.1 (Info and Relationships — semantic structure) ────────────
    ("1.3.1", "4.1.2"): SCRelationship.RELATED,       # Semantic structure helps name/role
    ("1.3.1", "1.1.1"): SCRelationship.UNRELATED,     # Table fix unrelated to alt text
    ("1.3.1", "2.4.3"): SCRelationship.POTENTIALLY_INTERACTING,  # Structural changes can affect tab order
    ("1.3.1", "1.4.3"): SCRelationship.UNRELATED,
    ("1.3.1", "2.4.7"): SCRelationship.UNRELATED,
    ("1.3.1", "3.1.1"): SCRelationship.UNRELATED,

    # ── Fixing 2.4.2 (Page Titled) ────────────────────────────────────────────
    # Adding <title> to <head>
    ("2.4.2", "4.1.2"): SCRelationship.UNRELATED,     # Title has no role/name interaction
    ("2.4.2", "1.3.1"): SCRelationship.UNRELATED,
    ("2.4.2", "1.1.1"): SCRelationship.UNRELATED,
    ("2.4.2", "2.4.3"): SCRelationship.UNRELATED,
    ("2.4.2", "2.4.7"): SCRelationship.UNRELATED,
    ("2.4.2", "3.1.1"): SCRelationship.UNRELATED,
    ("2.4.2", "1.4.3"): SCRelationship.UNRELATED,

    # ── Fixing 2.4.3 (Focus Order — tabindex) ────────────────────────────────
    # Fixing tabindex to 0
    ("2.4.3", "4.1.2"): SCRelationship.RELATED,       # Keyboard/focus closely tied to name/role
    ("2.4.3", "2.1.1"): SCRelationship.RELATED,       # Focus order and keyboard are siblings
    ("2.4.3", "2.4.7"): SCRelationship.RELATED,       # Focus order and focus visible are siblings
    ("2.4.3", "1.3.1"): SCRelationship.POTENTIALLY_INTERACTING,
    ("2.4.3", "1.1.1"): SCRelationship.UNRELATED,
    ("2.4.3", "3.1.1"): SCRelationship.UNRELATED,
    ("2.4.3", "1.4.3"): SCRelationship.UNRELATED,

    # ── Fixing 2.4.4 (Link Purpose) ──────────────────────────────────────────
    # Changing link text — requires human judgment (not auto-remediated)
    ("2.4.4", "4.1.2"): SCRelationship.RELATED,       # Link text is accessible name for 4.1.2
    ("2.4.4", "1.3.1"): SCRelationship.RELATED,
    ("2.4.4", "1.1.1"): SCRelationship.UNRELATED,
    ("2.4.4", "2.4.7"): SCRelationship.UNRELATED,
    ("2.4.4", "3.1.1"): SCRelationship.UNRELATED,

    # ── Fixing 2.4.7 (Focus Visible — outline:none) ──────────────────────────
    # Adding :focus-visible styles
    ("2.4.7", "4.1.2"): SCRelationship.RELATED,       # Both about interaction accessibility
    ("2.4.7", "2.4.3"): SCRelationship.RELATED,
    ("2.4.7", "2.1.1"): SCRelationship.RELATED,
    ("2.4.7", "1.4.3"): SCRelationship.POTENTIALLY_INTERACTING,  # Focus style color might affect contrast
    ("2.4.7", "1.4.11"): SCRelationship.POTENTIALLY_INTERACTING, # Non-text contrast for focus indicator
    ("2.4.7", "1.1.1"): SCRelationship.UNRELATED,
    ("2.4.7", "1.3.1"): SCRelationship.UNRELATED,
    ("2.4.7", "3.1.1"): SCRelationship.UNRELATED,

    # ── Fixing 3.1.1 (Language of Page — html lang) ──────────────────────────
    # Adding lang="en" — most benign fix possible
    ("3.1.1", "4.1.2"): SCRelationship.UNRELATED,
    ("3.1.1", "1.3.1"): SCRelationship.UNRELATED,
    ("3.1.1", "1.1.1"): SCRelationship.UNRELATED,
    ("3.1.1", "2.4.3"): SCRelationship.UNRELATED,
    ("3.1.1", "2.4.7"): SCRelationship.UNRELATED,
    ("3.1.1", "2.4.2"): SCRelationship.UNRELATED,
    ("3.1.1", "1.4.3"): SCRelationship.UNRELATED,
    ("3.1.1", "2.1.1"): SCRelationship.UNRELATED,

    # ── Fixing 4.1.2 (Name, Role, Value — button/input/aria) ─────────────────
    # Adding aria-label, aria-labelledby, or fixing role attributes
    ("4.1.2", "1.3.1"): SCRelationship.RELATED,       # aria-label is directly related to info structure
    ("4.1.2", "4.1.2"): SCRelationship.COMPATIBLE,    # Same SC
    ("4.1.2", "2.4.4"): SCRelationship.RELATED,       # Accessible name is link purpose
    ("4.1.2", "1.1.1"): SCRelationship.POTENTIALLY_INTERACTING,
    ("4.1.2", "2.4.3"): SCRelationship.POTENTIALLY_INTERACTING,  # Role changes can affect tab behavior
    ("4.1.2", "2.4.7"): SCRelationship.UNRELATED,     # aria-label doesn't affect outline
    ("4.1.2", "3.1.1"): SCRelationship.UNRELATED,     # aria-label doesn't affect lang
    ("4.1.2", "1.4.3"): SCRelationship.UNRELATED,     # aria-label doesn't affect color
    ("4.1.2", "1.4.11"): SCRelationship.UNRELATED,

    # ACTUAL CONFLICTS for 4.1.2 fixes:
    # If fixing button-name (4.1.2) by adding aria-hidden="true" → actual conflict with 4.1.2 itself
    # If adding role="presentation" to a table inside a button → conflicts with 1.3.1
    # These are caught by the CONTRADICTION_PATTERNS themselves; the relationship map
    # determines whether to BLOCK (ACTUAL_CONFLICT) or WARN (POTENTIALLY_INTERACTING).
}


# ── Patterns that are ALWAYS ACTUAL_CONFLICT regardless of which SC is fixed ──
#
# These patterns are so dangerous that they represent guaranteed violations
# regardless of what is being fixed. If the proposed HTML contains these
# patterns AND they are NOT the intentional fix, it is an ACTUAL_CONFLICT.

UNCONDITIONAL_CONFLICT_PATTERNS = {
    # Removing aria-hidden from an already-hidden but important element
    # is fine; but ADDING aria-hidden to an interactive element is always wrong
    "aria_hidden_on_interactive",  # Covered by pattern index 0 in _CONTRADICTION_PATTERNS
    # A div/span with onclick but no role='button' is never acceptable
    "div_span_onclick_no_role",    # Covered by pattern index 4
}


def classify_relationship(
    sc_being_fixed: str,
    sc_of_pattern: str,
    pattern_index: int = -1,
) -> SCRelationship:
    """
    Look up the relationship between two SCs.

    Args:
        sc_being_fixed:   The SC the remediation plan is addressing
        sc_of_pattern:    The SC that the contradiction pattern belongs to
        pattern_index:    Index in _CONTRADICTION_PATTERNS (-1 = unknown)

    Returns:
        SCRelationship enum value.
        Defaults to UNKNOWN if no explicit entry exists.
    """
    # Same SC → always COMPATIBLE (we're fixing it, not contradicting it)
    if sc_being_fixed == sc_of_pattern:
        return SCRelationship.COMPATIBLE

    # Unconditional conflict patterns are dangerous, BUT only flag as ACTUAL_CONFLICT
    # if the SC being fixed is actually related to the pattern's SC.
    # For example: fixing html-has-lang (3.1.1) should never be blocked because
    # the page already has aria-hidden somewhere (pattern 0 → violates 4.1.2).
    # We only block if the relationship table says these SCs actually interact.
    if pattern_index in (0, 1, 4):
        # Pattern 0, 4 → violates 4.1.2; Pattern 1 → violates 1.3.1
        pattern_sc = "4.1.2" if pattern_index in (0, 4) else "1.3.1"
        key = (sc_being_fixed, pattern_sc)
        rel = SC_RELATIONSHIPS.get(key)
        if rel is not None and rel not in (
            SCRelationship.UNRELATED,
            SCRelationship.COMPATIBLE,
            SCRelationship.RELATED,
        ):
            return SCRelationship.ACTUAL_CONFLICT
        # If the table says UNRELATED/COMPATIBLE/RELATED → not a real conflict
        if rel in (SCRelationship.UNRELATED, SCRelationship.COMPATIBLE, SCRelationship.RELATED):
            return rel
        # No entry in table → use POTENTIALLY_INTERACTING (warning, not block)
        return SCRelationship.POTENTIALLY_INTERACTING

    # Look up explicit relationship
    key = (sc_being_fixed, sc_of_pattern)
    relationship = SC_RELATIONSHIPS.get(key)

    if relationship is not None:
        return relationship

    # Default: UNKNOWN → treated as POTENTIALLY_INTERACTING by the caller
    return SCRelationship.UNKNOWN


def should_surface_contradiction(relationship: SCRelationship) -> bool:
    """
    Return True if this relationship should be reported as a contradiction.

    Only ACTUAL_CONFLICT surfaces to the user as a blocking issue.
    UNKNOWN uses POTENTIALLY_INTERACTING behavior (warning, not block).
    """
    return relationship == SCRelationship.ACTUAL_CONFLICT


def should_warn_contradiction(relationship: SCRelationship) -> bool:
    """
    Return True if this relationship should produce a non-blocking warning.
    """
    return relationship in (
        SCRelationship.ACTUAL_CONFLICT,
        SCRelationship.POTENTIALLY_INTERACTING,
        SCRelationship.UNKNOWN,
    )
