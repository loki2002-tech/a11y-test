"""
tests/golden_dataset/test_false_positives.py

Golden-dataset regression tests for FindingValidator.

These tests are the ground truth for the false-positive detection system.
Every test case encodes a human-verified accessibility finding with a known
expected verdict.  If FindingValidator's verdict ever disagrees with the
expected verdict, the tests catch it immediately — before any LLM or patch
work runs on incorrect data.

Test philosophy
---------------
* Each test is *self-contained* — no fixtures from external files.
* ``mock_context()`` constructs a minimal SourceContext-compatible object
  (a plain dict) so that FindingValidator can be exercised in pure unit-test
  style without any database, browser, or file system I/O.
* Assertions always check the ``verdict`` field.  Where the verdict is
  ambiguous by design (PARTIALLY_CONFIRMED, NOT_ENOUGH_EVIDENCE), the test
  also checks ``confidence`` to guard against over-confident verdicts.

Running
-------
::

    pytest tests/golden_dataset/test_false_positives.py -v

Adding new cases
----------------
Copy any existing test function, change the finding_data / mock_context args,
set the expected verdict, and add a brief docstring explaining the real-world
scenario the case represents.
"""

from __future__ import annotations

import sys
import os
from typing import Any

import pytest

# Ensure the src tree is on the path when running pytest from the repo root
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src")
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from accessibility_agent.remediation.finding_validator import (
    FindingValidator,
    FindingValidationResult,
)


# ── Helper ────────────────────────────────────────────────────────────────────


def mock_context(
    element_attributes: dict[str, str],
    nearby_labels: list[str] | None = None,
    block_source: str = "",
    element_tag: str = "",
    is_decorative: bool = False,
) -> dict[str, Any]:
    """
    Build a minimal source-context dict compatible with ``FindingValidator``.

    This mirrors the shape of ``SourceContext`` (``remediation/schemas.py``)
    but is a plain ``dict`` so tests have zero infrastructure dependencies.

    Parameters
    ----------
    element_attributes:
        Parsed HTML attributes of the problematic element, e.g.
        ``{'id': 'email', 'type': 'email'}``.
    nearby_labels:
        List of label strings found near the element.  Each string may
        contain a ``for="…"`` attribute, e.g. ``['for="email"']``.
        Defaults to an empty list.
    block_source:
        Raw HTML block surrounding the element (±15 lines).  Used for
        text-content extraction and in-block label scanning.
    element_tag:
        Lower-cased HTML tag of the element, e.g. ``'input'``, ``'button'``.
    is_decorative:
        Whether the element is flagged as decorative (used for image-alt
        empty-alt false-positive detection).

    Returns
    -------
    dict
        A context dict accepted by all ``FindingValidator`` internal helpers.
    """
    return {
        "element_attributes": element_attributes,
        "nearby_labels": nearby_labels if nearby_labels is not None else [],
        "block_source": block_source,
        "element_tag": element_tag,
        "is_decorative": is_decorative,
    }


# ── Shared validator instance (stateless — safe to reuse) ─────────────────────

_validator = FindingValidator()


# ══════════════════════════════════════════════════════════════════════════════
# FALSE POSITIVE cases
# ══════════════════════════════════════════════════════════════════════════════


def test_input_with_native_label_is_false_positive() -> None:
    """
    TC-FP-001: Input element with a correctly associated native <label>.

    The scanner fires ``label`` because it does not resolve the for/id
    association in all page configurations.  The validator must detect
    the matching ``for="email"`` in nearby_labels and return FALSE_POSITIVE.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-FP-001",
        "rule_id": "label",
        "element": {
            "html": '<input id="email" type="email">',
            "selector": "#email",
        },
        "description": "Form element does not have an accessible label",
        "wcag": {"success_criterion": "1.3.1"},
    }
    source_context_attrs = {"id": "email", "type": "email"}
    nearby_labels = ['for="email"']  # label with for=email exists

    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(source_context_attrs, nearby_labels, element_tag="input"),
    )

    assert result.verdict == "FALSE_POSITIVE", (
        f"Expected FALSE_POSITIVE for input with native label, got {result.verdict!r}.\n"
        f"Reason: {result.reason}"
    )
    assert result.confidence >= 0.90, (
        f"Confidence {result.confidence} too low for a clear false positive."
    )


def test_button_with_visible_text_is_false_positive() -> None:
    """
    TC-FP-002: Button with visible text content.

    Scanners occasionally misfire on buttons when the text is inside a
    child span.  The validator should extract visible text from the HTML
    block and return FALSE_POSITIVE.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-FP-002",
        "rule_id": "button-name",
        "element": {
            "html": "<button>Save</button>",
            "selector": "button",
        },
        "description": "Buttons must have discernible text",
        "wcag": {"success_criterion": "4.1.2"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"id": "save-btn"},
            [],
            block_source="<button>Save</button>",
            element_tag="button",
        ),
    )

    assert result.verdict == "FALSE_POSITIVE", (
        f"Expected FALSE_POSITIVE for button with text 'Save', got {result.verdict!r}.\n"
        f"Reason: {result.reason}"
    )


def test_input_with_aria_label_is_false_positive() -> None:
    """
    TC-FP-003: Input element labelled via aria-label.

    aria-label provides an accessible name via the ARIA spec — this is an
    explicitly supported labelling technique and must not be flagged.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-FP-003",
        "rule_id": "label",
        "element": {
            "html": '<input aria-label="Email address" type="email">',
            "selector": "input",
        },
        "description": "Form element does not have an accessible label",
        "wcag": {"success_criterion": "1.3.1"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"aria-label": "Email address", "type": "email"},
            [],
            element_tag="input",
        ),
    )

    assert result.verdict == "FALSE_POSITIVE", (
        f"Expected FALSE_POSITIVE for input with aria-label, got {result.verdict!r}.\n"
        f"Reason: {result.reason}"
    )
    assert result.confidence >= 0.90


def test_semantic_main_landmark_is_false_positive() -> None:
    """
    TC-FP-004: <main> element flagged as missing landmark.

    <main> is a native landmark element — it implicitly maps to the
    'main' landmark role in all modern browsers/AT.  Any scanner that
    reports it as needing a role has fired a false positive.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-FP-004",
        "rule_id": "region",
        "element": {
            "html": "<main>",
            "selector": "main",
        },
        "description": "All page content should be contained in landmarks",
        "wcag": {"success_criterion": "1.3.1"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context({}, [], block_source="<main>", element_tag="main"),
    )

    assert result.verdict == "FALSE_POSITIVE", (
        f"Expected FALSE_POSITIVE for <main> landmark, got {result.verdict!r}.\n"
        f"Reason: {result.reason}"
    )


def test_input_with_aria_labelledby_is_false_positive() -> None:
    """
    TC-FP-005: Input labelled via aria-labelledby pointing to an existing element.

    The validator should resolve the ID reference in the block source and
    confirm an accessible name exists.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-FP-005",
        "rule_id": "label",
        "element": {
            "html": '<input aria-labelledby="search-label" type="search">',
            "selector": "#search",
        },
        "description": "Form element does not have an accessible label",
        "wcag": {"success_criterion": "1.3.1"},
    }
    block = '<span id="search-label">Search the site</span><input aria-labelledby="search-label" type="search">'
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"aria-labelledby": "search-label", "type": "search"},
            [],
            block_source=block,
            element_tag="input",
        ),
    )

    assert result.verdict == "FALSE_POSITIVE", (
        f"Expected FALSE_POSITIVE for input with aria-labelledby, got {result.verdict!r}.\n"
        f"Reason: {result.reason}"
    )


def test_button_with_aria_label_is_false_positive() -> None:
    """
    TC-FP-006: Icon-only button labelled via aria-label.

    A common pattern for icon buttons — the aria-label provides the
    accessible name and the scanner should not have flagged this.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-FP-006",
        "rule_id": "button-name",
        "element": {
            "html": '<button aria-label="Close dialog"><svg aria-hidden="true">…</svg></button>',
            "selector": "button.close",
        },
        "description": "Buttons must have discernible text",
        "wcag": {"success_criterion": "4.1.2"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"aria-label": "Close dialog"},
            [],
            block_source='<button aria-label="Close dialog"><svg aria-hidden="true">…</svg></button>',
            element_tag="button",
        ),
    )

    assert result.verdict == "FALSE_POSITIVE", (
        f"Expected FALSE_POSITIVE for button with aria-label, got {result.verdict!r}.\n"
        f"Reason: {result.reason}"
    )


def test_button_with_svg_title_is_false_positive() -> None:
    """
    TC-FP-007: Icon-only button where the SVG contains a <title>.

    The SVG <title> provides an accessible name for icon-only buttons.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-FP-007",
        "rule_id": "button-name",
        "element": {
            "html": "<button><svg><title>Open menu</title><path d='…'/></svg></button>",
            "selector": "button.hamburger",
        },
        "description": "Buttons must have discernible text",
        "wcag": {"success_criterion": "4.1.2"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {},
            [],
            block_source="<button><svg><title>Open menu</title><path d='…'/></svg></button>",
            element_tag="button",
        ),
    )

    assert result.verdict == "FALSE_POSITIVE", (
        f"Expected FALSE_POSITIVE for button with SVG title, got {result.verdict!r}.\n"
        f"Reason: {result.reason}"
    )


def test_decorative_image_empty_alt_is_false_positive() -> None:
    """
    TC-FP-008: Decorative image with alt="" (the correct technique).

    Per WCAG 1.1.1 Technique H67, decorative images should have alt=""
    so AT skips them.  A scanner that flags this has fired a false positive.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-FP-008",
        "rule_id": "image-alt",
        "element": {
            "html": '<img src="divider.png" alt="">',
            "selector": ".divider img",
        },
        "description": "Images must have alternative text",
        "wcag": {"success_criterion": "1.1.1"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"src": "divider.png", "alt": ""},
            [],
            element_tag="img",
            is_decorative=True,
        ),
    )

    assert result.verdict == "FALSE_POSITIVE", (
        f"Expected FALSE_POSITIVE for decorative image with alt='', got {result.verdict!r}.\n"
        f"Reason: {result.reason}"
    )


def test_nav_element_is_false_positive_landmark() -> None:
    """
    TC-FP-009: <nav> element flagged as missing landmark.

    <nav> is a native landmark — maps to 'navigation' role.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-FP-009",
        "rule_id": "region",
        "element": {"html": "<nav>", "selector": "nav"},
        "description": "All page content should be contained in landmarks",
        "wcag": {"success_criterion": "1.3.1"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context({}, [], block_source="<nav>", element_tag="nav"),
    )
    assert result.verdict == "FALSE_POSITIVE"


def test_section_with_aria_label_is_false_positive_landmark() -> None:
    """
    TC-FP-010: <section aria-label="…"> maps to the 'region' landmark role.

    A labeled <section> must not be flagged as missing a landmark.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-FP-010",
        "rule_id": "region",
        "element": {
            "html": '<section aria-label="Product features">',
            "selector": "section.features",
        },
        "description": "All page content should be contained in landmarks",
        "wcag": {"success_criterion": "1.3.1"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"aria-label": "Product features"},
            [],
            element_tag="section",
        ),
    )
    assert result.verdict == "FALSE_POSITIVE"


def test_label_via_for_in_block_source_is_false_positive() -> None:
    """
    TC-FP-011: Label association present in block_source (not nearby_labels).

    When nearby_labels is empty but the block_source contains
    a matching <label for="…">, the validator must still detect it.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-FP-011",
        "rule_id": "label",
        "element": {
            "html": '<input id="username" type="text">',
            "selector": "#username",
        },
        "description": "Form element does not have an accessible label",
        "wcag": {"success_criterion": "1.3.1"},
    }
    block = '<label for="username">Username</label><input id="username" type="text">'
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"id": "username", "type": "text"},
            [],  # nearby_labels is empty — must scan block_source
            block_source=block,
            element_tag="input",
        ),
    )
    assert result.verdict == "FALSE_POSITIVE", (
        f"Expected FALSE_POSITIVE when label is in block_source, got {result.verdict!r}."
    )


def test_image_with_meaningful_alt_is_false_positive() -> None:
    """
    TC-FP-012: Image with meaningful alt text.

    The scanner may flag images even when they have valid alt text.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-FP-012",
        "rule_id": "image-alt",
        "element": {
            "html": '<img src="hero.jpg" alt="A diverse team collaborating in a modern office">',
            "selector": ".hero img",
        },
        "description": "Images must have alternative text",
        "wcag": {"success_criterion": "1.1.1"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"src": "hero.jpg", "alt": "A diverse team collaborating in a modern office"},
            [],
            element_tag="img",
        ),
    )
    assert result.verdict == "FALSE_POSITIVE"


# ══════════════════════════════════════════════════════════════════════════════
# CONFIRMED (true positive) cases
# ══════════════════════════════════════════════════════════════════════════════


def test_image_missing_alt_is_confirmed() -> None:
    """
    TC-TP-001: Informational image missing the alt attribute entirely.

    No alt attribute = CONFIRMED violation.  The scanner is correct.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-TP-001",
        "rule_id": "image-alt",
        "element": {
            "html": '<img src="hero.jpg">',
            "selector": ".hero img",
        },
        "description": "Images must have alternative text",
        "wcag": {"success_criterion": "1.1.1"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context({"src": "hero.jpg"}, [], element_tag="img"),
    )

    assert result.verdict == "CONFIRMED", (
        f"Expected CONFIRMED for image missing alt, got {result.verdict!r}.\n"
        f"Reason: {result.reason}"
    )
    assert result.confidence >= 0.90


def test_input_with_no_label_mechanism_is_confirmed() -> None:
    """
    TC-TP-002: Input with no label, no aria-label, no aria-labelledby.

    All accessible name mechanisms are absent — genuine violation.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-TP-002",
        "rule_id": "label",
        "element": {
            "html": '<input type="text" placeholder="Enter name">',
            "selector": "input.name",
        },
        "description": "Form element does not have an accessible label",
        "wcag": {"success_criterion": "1.3.1"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"type": "text", "placeholder": "Enter name"},
            [],
            block_source='<input type="text" placeholder="Enter name">',
            element_tag="input",
        ),
    )

    assert result.verdict == "CONFIRMED", (
        f"Expected CONFIRMED for unlabelled input, got {result.verdict!r}.\n"
        f"Reason: {result.reason}"
    )


def test_button_with_no_name_is_confirmed() -> None:
    """
    TC-TP-003: Truly empty button — no text, no ARIA attributes.

    A button element with no content at all is a genuine violation.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-TP-003",
        "rule_id": "button-name",
        "element": {
            "html": "<button></button>",
            "selector": "button.icon",
        },
        "description": "Buttons must have discernible text",
        "wcag": {"success_criterion": "4.1.2"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {},
            [],
            block_source="<button></button>",
            element_tag="button",
        ),
    )

    assert result.verdict == "CONFIRMED", (
        f"Expected CONFIRMED for empty button, got {result.verdict!r}.\n"
        f"Reason: {result.reason}"
    )


def test_aria_hidden_focus_is_always_confirmed() -> None:
    """
    TC-TP-004: aria-hidden-focus is ALWAYS a real violation.

    There is no valid configuration where an interactive element inside
    an aria-hidden subtree is accessible — this rule has no false positives.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-TP-004",
        "rule_id": "aria-hidden-focus",
        "element": {
            "html": '<button aria-hidden="true" tabindex="0">Submit</button>',
            "selector": "div[aria-hidden] button",
        },
        "description": "Interactive element should not be focusable when hidden from AT",
        "wcag": {"success_criterion": "4.1.2"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"aria-hidden": "true", "tabindex": "0"},
            [],
            element_tag="button",
        ),
    )

    assert result.verdict == "CONFIRMED", (
        f"Expected CONFIRMED for aria-hidden-focus, got {result.verdict!r}."
    )
    assert result.confidence == 1.0, (
        "aria-hidden-focus should be 100% confident — no false positives exist."
    )


def test_image_with_generic_alt_is_confirmed() -> None:
    """
    TC-TP-005: Image with alt="image" — generic, meaningless placeholder.

    A placeholder alt value conveys nothing to AT users — equivalent to
    missing alt for informational content.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-TP-005",
        "rule_id": "image-alt",
        "element": {
            "html": '<img src="product.jpg" alt="image">',
            "selector": ".product img",
        },
        "description": "Images must have alternative text",
        "wcag": {"success_criterion": "1.1.1"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"src": "product.jpg", "alt": "image"},
            [],
            element_tag="img",
        ),
    )

    assert result.verdict == "CONFIRMED", (
        f"Expected CONFIRMED for image with generic alt='image', got {result.verdict!r}."
    )


# ══════════════════════════════════════════════════════════════════════════════
# MANUAL_REVIEW_REQUIRED cases
# ══════════════════════════════════════════════════════════════════════════════


def test_accordion_div_is_manual_review() -> None:
    """
    TC-MR-001: Accordion div with onclick — complex disclosure widget.

    A <div> acting as an accordion header with onclick and aria-expanded
    requires review of the full keyboard interaction model (Enter/Space,
    arrow keys, Escape) and cannot be safely auto-remediated.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-MANUAL-001",
        "rule_id": "button-name",
        "element": {
            "html": '<div class="accordion-header" aria-expanded="false" onclick="toggleAcc(this)">Have a promo code?</div>',
            "selector": "div.accordion-header",
        },
        "description": "Interactive control does not have accessible name",
        "wcag": {"success_criterion": "4.1.2"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {
                "class": "accordion-header",
                "aria-expanded": "false",
                "onclick": "toggleAcc(this)",
            },
            [],
            element_tag="div",
        ),
    )

    assert result.verdict == "MANUAL_REVIEW_REQUIRED", (
        f"Expected MANUAL_REVIEW_REQUIRED for accordion div, got {result.verdict!r}.\n"
        f"Reason: {result.reason}"
    )


def test_div_with_onclick_is_manual_review() -> None:
    """
    TC-MR-002: Plain <div> with onclick handler (no ARIA role).

    Any non-native interactive element driven by onclick is a custom widget
    and must be manually reviewed for the full ARIA role + keyboard pattern.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-MANUAL-002",
        "rule_id": "button-name",
        "element": {
            "html": '<div onclick="handleClick()">Click me</div>',
            "selector": "div.clickable",
        },
        "description": "Interactive control does not have accessible name",
        "wcag": {"success_criterion": "4.1.2"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"onclick": "handleClick()"},
            [],
            block_source='<div onclick="handleClick()">Click me</div>',
            element_tag="div",
        ),
    )

    assert result.verdict == "MANUAL_REVIEW_REQUIRED", (
        f"Expected MANUAL_REVIEW_REQUIRED for div with onclick, got {result.verdict!r}."
    )


# ══════════════════════════════════════════════════════════════════════════════
# PARTIALLY_CONFIRMED / edge cases
# ══════════════════════════════════════════════════════════════════════════════


def test_unknown_rule_returns_partially_confirmed() -> None:
    """
    TC-EDGE-001: Rule with no deterministic handler → PARTIALLY_CONFIRMED.

    The default fallback should not crash and must set a low confidence
    score to signal that human review is advisable.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-EDGE-001",
        "rule_id": "color-contrast",
        "element": {
            "html": '<p style="color: #777; background: white;">Some text</p>',
            "selector": "p.muted",
        },
        "description": "Elements must meet minimum color contrast ratio",
        "wcag": {"success_criterion": "1.4.3"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"style": "color: #777; background: white;"},
            [],
            element_tag="p",
        ),
    )

    assert result.verdict == "PARTIALLY_CONFIRMED", (
        f"Expected PARTIALLY_CONFIRMED for unknown rule, got {result.verdict!r}."
    )
    # Unknown rules should express uncertainty via a low confidence score
    assert result.confidence <= 0.75, (
        f"Confidence {result.confidence} is too high for an unsupported rule type."
    )


def test_heading_order_skip_is_confirmed() -> None:
    """
    TC-EDGE-002: Heading that skips from h1 to h4 — CONFIRMED violation.

    The block source contains h1 immediately followed by h4 with no
    intermediate h2 or h3.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-EDGE-002",
        "rule_id": "heading-order",
        "element": {
            "html": "<h4>Sub-section heading</h4>",
            "selector": "h4.sub",
        },
        "description": "Heading levels should only increase by one",
        "wcag": {"success_criterion": "1.3.1"},
    }
    block = "<h1>Page title</h1><p>Intro text</p><h4>Sub-section heading</h4>"
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {},
            [],
            block_source=block,
            element_tag="h4",
        ),
    )

    assert result.verdict == "CONFIRMED", (
        f"Expected CONFIRMED for h1→h4 skip, got {result.verdict!r}.\n"
        f"Reason: {result.reason}"
    )


def test_element_with_existing_role_landmark_is_false_positive() -> None:
    """
    TC-EDGE-003: Element already has an explicit landmark role attribute.

    If the element has role="banner" (or any landmark role) the scanner
    should not have fired — this is a false positive.
    """
    finding_data: dict[str, Any] = {
        "finding_id": "TEST-EDGE-003",
        "rule_id": "region",
        "element": {
            "html": '<div role="banner">…</div>',
            "selector": "div.header",
        },
        "description": "All page content should be contained in landmarks",
        "wcag": {"success_criterion": "1.3.1"},
    }
    result: FindingValidationResult = _validator.validate(
        finding_data,
        mock_context(
            {"role": "banner"},
            [],
            element_tag="div",
        ),
    )

    assert result.verdict == "FALSE_POSITIVE", (
        f"Expected FALSE_POSITIVE for element with explicit role='banner', got {result.verdict!r}."
    )


def test_validation_result_invalid_verdict_raises() -> None:
    """
    TC-EDGE-004: FindingValidationResult rejects invalid verdict strings.

    The dataclass __post_init__ must validate the verdict field and raise
    ValueError for any string not in the allowed set.
    """
    with pytest.raises(ValueError, match="Invalid verdict"):
        FindingValidationResult(
            verdict="WRONG_VERDICT",
            reason="This should fail",
            confidence=0.5,
        )


def test_validation_result_invalid_confidence_raises() -> None:
    """
    TC-EDGE-005: FindingValidationResult rejects out-of-range confidence values.
    """
    with pytest.raises(ValueError, match="confidence must be in"):
        FindingValidationResult(
            verdict="CONFIRMED",
            reason="Confidence out of range",
            confidence=1.5,  # > 1.0 — invalid
        )
