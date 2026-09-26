"""
remediation/finding_validator.py — Stage 3 / 4 of the Remediation Pipeline.

Deterministically validates whether a scanner finding is a REAL violation or a
FALSE POSITIVE *before* any LLM call or patch-generation work is performed.

Design principles
-----------------
* Zero AI / LLM calls — every decision is rule-based and reproducible.
* No I/O side-effects — all inputs are plain Python dicts or ``SourceContext``
  (or duck-typed equivalents accepted via ``source_context``).
* All verdicts carry a numeric ``confidence`` so the caller can threshold on
  certainty rather than treating every result as binary.

Verdict vocabulary
------------------
CONFIRMED               — The finding is a genuine accessibility violation.
FALSE_POSITIVE          — The scanner raised an alert that is not a real issue.
PARTIALLY_CONFIRMED     — Some evidence supports the finding; full automation
                         is inadvisable but the finding is not fabricated.
MANUAL_REVIEW_REQUIRED  — The element is a complex interactive widget whose
                         accessibility cannot be determined without behavioural
                         or visual inspection.
NOT_ENOUGH_EVIDENCE     — The validator lacks sufficient context (e.g., missing
                         block_source) to decide either way.

Integration
-----------
``FindingValidator.validate()`` is called by the remediation agent immediately
after source location is confirmed (Stage 3) and before classification /
planning (Stages 4+).  The ``FindingValidationResult`` is stored on the
``RemediationResult.audit_trace`` so every decision is traceable.

Usage example::

    from accessibility_agent.remediation.finding_validator import FindingValidator

    validator = FindingValidator()
    result = validator.validate(finding_data, source_context)
    if result.verdict == "FALSE_POSITIVE":
        # Skip to next finding — do not spend LLM tokens
        ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ── Result Dataclass ──────────────────────────────────────────────────────────


@dataclass
class FindingValidationResult:
    """
    Output of ``FindingValidator.validate()``.

    Attributes
    ----------
    verdict:
        One of ``'CONFIRMED'``, ``'FALSE_POSITIVE'``,
        ``'PARTIALLY_CONFIRMED'``, ``'MANUAL_REVIEW_REQUIRED'``,
        ``'NOT_ENOUGH_EVIDENCE'``.
    reason:
        Human-readable one-sentence explanation of the verdict.
    details:
        Structured evidence collected during validation.  Keys vary by
        rule type but always include ``rule_id`` and ``checks_run``.
    confidence:
        Float in ``[0.0, 1.0]``.  Deterministic rules that find clear
        evidence emit 0.95–1.0.  Partial or heuristic evidence emits
        0.50–0.80.
    """

    verdict: str  # CONFIRMED | FALSE_POSITIVE | PARTIALLY_CONFIRMED | MANUAL_REVIEW_REQUIRED | NOT_ENOUGH_EVIDENCE
    reason: str
    details: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    def __post_init__(self) -> None:
        _valid_verdicts = {
            "CONFIRMED",
            "FALSE_POSITIVE",
            "PARTIALLY_CONFIRMED",
            "MANUAL_REVIEW_REQUIRED",
            "NOT_ENOUGH_EVIDENCE",
        }
        if self.verdict not in _valid_verdicts:
            raise ValueError(
                f"Invalid verdict {self.verdict!r}. Must be one of {_valid_verdicts}."
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence}."
            )


# ── Generic label/alt placeholder sets ───────────────────────────────────────

#: Alt text values that are meaningless / generic — treated as missing alt.
_GENERIC_ALT_TEXT: frozenset[str] = frozenset(
    {
        "image",
        "photo",
        "picture",
        "pic",
        "img",
        "figure",
        "graphic",
        "logo",
        "icon",
        "banner",
        "thumbnail",
        "placeholder",
        "spacer",
        " ",
        "",
    }
)

#: Semantic HTML elements that inherently define landmark regions.
_LANDMARK_TAGS: frozenset[str] = frozenset(
    {"main", "nav", "aside", "header", "footer", "form", "section"}
)

#: Elements that are interactive by nature and must always have an acc. name.
_INTERACTIVE_TAGS: frozenset[str] = frozenset(
    {"button", "a", "input", "select", "textarea", "details", "summary"}
)

#: Heading tag names.
_HEADING_TAGS: frozenset[str] = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


# ── Helpers ───────────────────────────────────────────────────────────────────


def _attr(source_context: Any, name: str) -> str:
    """
    Safely retrieve an element attribute from ``source_context``.

    Accepts both dict-like objects (used in tests) and Pydantic
    ``SourceContext`` instances.  Always returns a string (empty string
    if not found or if the context type is unrecognised).
    """
    try:
        attrs: dict[str, str] = (
            source_context.element_attributes
            if hasattr(source_context, "element_attributes")
            else source_context.get("element_attributes", {})
        )
        return attrs.get(name, "")
    except Exception:
        return ""


def _block(source_context: Any) -> str:
    """Return the block_source string from the context, or empty string."""
    try:
        if hasattr(source_context, "block_source"):
            return source_context.block_source or ""
        return source_context.get("block_source", "")
    except Exception:
        return ""


def _tag(source_context: Any) -> str:
    """Return the element_tag (lower-cased) from the context."""
    try:
        raw = (
            source_context.element_tag
            if hasattr(source_context, "element_tag")
            else source_context.get("element_tag", "")
        )
        return (raw or "").lower().strip()
    except Exception:
        return ""


def _nearby_labels(source_context: Any) -> list[str]:
    """Return the nearby_labels list from the context."""
    try:
        if hasattr(source_context, "nearby_labels"):
            return source_context.nearby_labels or []
        return source_context.get("nearby_labels", [])
    except Exception:
        return []


def _element_html(finding_data: dict[str, Any]) -> str:
    """Return the element HTML snippet from the raw finding dict."""
    return (
        finding_data.get("element", {}).get("html", "")
        if isinstance(finding_data.get("element"), dict)
        else ""
    )


# ── Core Validator ────────────────────────────────────────────────────────────


class FindingValidator:
    """
    Stage 3 / 4 deterministic gate in the remediation pipeline.

    Instantiate once and reuse — the class holds no mutable state.

    Example
    -------
    ::

        validator = FindingValidator()
        result = validator.validate(finding_data, source_context)
        print(result.verdict, result.confidence)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        finding_data: dict[str, Any],
        source_context: Any,
    ) -> FindingValidationResult:
        """
        Validate a scanner finding deterministically.

        Parameters
        ----------
        finding_data:
            Raw finding dict, typically sourced from an axe-core report or
            internal scanner.  Must contain at least ``rule_id``.
        source_context:
            Either a ``SourceContext`` Pydantic model (from
            ``remediation.schemas``) or a plain dict with the same fields.
            The validator accesses: ``element_attributes``, ``block_source``,
            ``element_tag``, ``nearby_labels``.

        Returns
        -------
        FindingValidationResult
            The deterministic verdict plus collected evidence.
        """
        rule_id: str = finding_data.get("rule_id", "").lower().strip()
        element_attrs: dict[str, str] = (
            source_context.element_attributes
            if hasattr(source_context, "element_attributes")
            else source_context.get("element_attributes", {})
        )
        block_src: str = _block(source_context)
        element_tag: str = _tag(source_context)
        labels: list[str] = _nearby_labels(source_context)
        element_html_str: str = _element_html(finding_data)

        # Dispatch to rule-specific handlers
        if rule_id in ("label", "label-content-name-mismatch"):
            return self._validate_label_rule(
                rule_id, element_attrs, labels, block_src, element_tag
            )
        if rule_id == "button-name":
            return self._validate_button_name(
                element_attrs, block_src, element_tag, element_html_str
            )
        if rule_id == "aria-hidden-focus":
            return self._validate_aria_hidden_focus(element_attrs)
        if rule_id == "region":
            return self._validate_region(element_attrs, element_tag, block_src)
        if rule_id == "image-alt":
            return self._validate_image_alt(element_attrs, source_context)
        if rule_id == "heading-order":
            return self._validate_heading_order(element_attrs, element_tag, block_src)

        # Accordion / disclosure / custom-interactive heuristic
        if self._is_accordion_or_disclosure(element_attrs, element_tag, block_src):
            return FindingValidationResult(
                verdict="MANUAL_REVIEW_REQUIRED",
                reason=(
                    "Element appears to be a disclosure or accordion widget "
                    "(div/span with onclick/aria-expanded). "
                    "Complex widget state requires behavioural analysis."
                ),
                details={
                    "rule_id": rule_id,
                    "element_tag": element_tag,
                    "checks_run": ["accordion_disclosure_heuristic"],
                },
                confidence=0.80,
            )

        # Default fallback
        return FindingValidationResult(
            verdict="PARTIALLY_CONFIRMED",
            reason=(
                "Scanner reported this issue; deterministic validation "
                "unavailable for this rule."
            ),
            details={
                "rule_id": rule_id,
                "element_tag": element_tag,
                "checks_run": ["default_fallback"],
            },
            confidence=0.50,
        )

    # ------------------------------------------------------------------
    # Rule-specific validators (private)
    # ------------------------------------------------------------------

    def _validate_label_rule(
        self,
        rule_id: str,
        element_attrs: dict[str, str],
        labels: list[str],
        block_src: str,
        element_tag: str,
    ) -> FindingValidationResult:
        """
        Handle ``label`` and ``label-content-name-mismatch`` rules.

        A finding is a FALSE_POSITIVE if:
        - The element carries ``aria-label`` (non-empty), OR
        - The element carries ``aria-labelledby`` (non-empty), OR
        - A ``<label for="...">`` in ``nearby_labels`` targets the element's
          ``id`` attribute.

        Only returns CONFIRMED when none of the above mechanisms are found.
        """
        checks: dict[str, Any] = {
            "rule_id": rule_id,
            "checks_run": ["aria_label", "aria_labelledby", "native_label_for"],
        }

        aria_label: str = element_attrs.get("aria-label", "").strip()
        aria_labelledby: str = element_attrs.get("aria-labelledby", "").strip()
        element_id: str = element_attrs.get("id", "").strip()

        # 1. aria-label present and non-empty
        if aria_label:
            checks["aria_label_found"] = aria_label
            return FindingValidationResult(
                verdict="FALSE_POSITIVE",
                reason=(
                    f"Element has aria-label={aria_label!r} — accessible name "
                    "is provided via ARIA attribute."
                ),
                details=checks,
                confidence=0.97,
            )

        # 2. aria-labelledby present and non-empty
        if aria_labelledby:
            checks["aria_labelledby_found"] = aria_labelledby
            return FindingValidationResult(
                verdict="FALSE_POSITIVE",
                reason=(
                    f"Element has aria-labelledby={aria_labelledby!r} — accessible name "
                    "is provided via ARIA association."
                ),
                details=checks,
                confidence=0.95,
            )

        # 3. Native <label for="id"> targeting this element
        if element_id:
            for label_text in labels:
                # Match patterns like: for="email", for='email', for=email
                if re.search(
                    rf"""for\s*=\s*["']?{re.escape(element_id)}["']?""",
                    label_text,
                    re.IGNORECASE,
                ):
                    checks["native_label_for_found"] = label_text
                    checks["matched_element_id"] = element_id
                    return FindingValidationResult(
                        verdict="FALSE_POSITIVE",
                        reason=(
                            f"A <label for=\"{element_id}\"> is present in nearby_labels "
                            "— the element has a native label association."
                        ),
                        details=checks,
                        confidence=0.96,
                    )

        # 4. Also scan block_source for a label tag with matching for= attribute
        if element_id and block_src:
            pattern = rf"""<label\b[^>]*\bfor\s*=\s*["']?{re.escape(element_id)}["']?"""
            if re.search(pattern, block_src, re.IGNORECASE):
                checks["native_label_in_block"] = True
                checks["matched_element_id"] = element_id
                return FindingValidationResult(
                    verdict="FALSE_POSITIVE",
                    reason=(
                        f"A <label for=\"{element_id}\"> was found in the surrounding "
                        "block source — the element has a native label association."
                    ),
                    details=checks,
                    confidence=0.93,
                )

        # No accessible name mechanism found → confirmed violation
        checks["no_accessible_name_found"] = True
        return FindingValidationResult(
            verdict="CONFIRMED",
            reason=(
                "No aria-label, aria-labelledby, or native <label for> association "
                "found for this form element."
            ),
            details=checks,
            confidence=0.95,
        )

    def _validate_button_name(
        self,
        element_attrs: dict[str, str],
        block_src: str,
        element_tag: str,
        element_html_str: str,
    ) -> FindingValidationResult:
        """
        Handle ``button-name`` rule.

        A button has an accessible name (FALSE_POSITIVE) when any of:
        - It contains visible text in its HTML/block source
        - ``aria-label`` attribute is present and non-empty
        - ``aria-labelledby`` attribute is present and non-empty (referencing
          an ID that exists in the block)
        - ``title`` attribute is non-empty
        - Contains an SVG ``<title>`` child element (icon-only pattern)

        For div/span acting as buttons (onclick on non-button tag):
        these are ``MANUAL_REVIEW_REQUIRED`` — handled by accordion heuristic
        called upstream.
        """
        checks: dict[str, Any] = {
            "rule_id": "button-name",
            "element_tag": element_tag,
            "checks_run": [
                "aria_label",
                "aria_labelledby",
                "title_attr",
                "svg_title",
                "visible_text",
            ],
        }

        # Non-button interactive elements on div/span → defer to caller
        if element_tag in ("div", "span") and (
            element_attrs.get("onclick") or "onclick" in block_src
        ):
            return FindingValidationResult(
                verdict="MANUAL_REVIEW_REQUIRED",
                reason=(
                    "Non-native interactive element (div/span with onclick). "
                    "Full widget interaction model must be reviewed."
                ),
                details=checks,
                confidence=0.85,
            )

        # 1. aria-label
        aria_label = element_attrs.get("aria-label", "").strip()
        if aria_label:
            checks["aria_label_found"] = aria_label
            return FindingValidationResult(
                verdict="FALSE_POSITIVE",
                reason=f"Button has aria-label={aria_label!r} — accessible name present.",
                details=checks,
                confidence=0.97,
            )

        # 2. aria-labelledby → check that the referenced ID exists in block
        aria_labelledby = element_attrs.get("aria-labelledby", "").strip()
        if aria_labelledby:
            ref_ids = aria_labelledby.split()
            found_ids = [
                rid
                for rid in ref_ids
                if re.search(
                    rf"""id\s*=\s*["']?{re.escape(rid)}["']?""",
                    block_src,
                    re.IGNORECASE,
                )
            ]
            checks["aria_labelledby_refs"] = ref_ids
            checks["aria_labelledby_resolved"] = found_ids
            if found_ids:
                return FindingValidationResult(
                    verdict="FALSE_POSITIVE",
                    reason=(
                        f"Button aria-labelledby={aria_labelledby!r} resolves to "
                        f"IDs {found_ids} in the surrounding block."
                    ),
                    details=checks,
                    confidence=0.94,
                )

        # 3. title attribute
        title = element_attrs.get("title", "").strip()
        if title:
            checks["title_found"] = title
            return FindingValidationResult(
                verdict="FALSE_POSITIVE",
                reason=f"Button has title={title!r} — accessible name provided via title.",
                details=checks,
                confidence=0.88,
            )

        # 4. SVG <title> child (icon-only button pattern)
        source_to_scan = element_html_str or block_src
        svg_title_match = re.search(
            r"<title\b[^>]*>([^<]+)</title>", source_to_scan, re.IGNORECASE
        )
        if svg_title_match:
            svg_title_text = svg_title_match.group(1).strip()
            if svg_title_text:
                checks["svg_title_found"] = svg_title_text
                return FindingValidationResult(
                    verdict="FALSE_POSITIVE",
                    reason=(
                        f"Button contains SVG <title>{svg_title_text}</title> — "
                        "accessible name provided for icon-only button."
                    ),
                    details=checks,
                    confidence=0.90,
                )

        # 5. Visible text content inside the element
        accname = self._compute_accname(element_attrs, source_to_scan, element_tag)
        if accname:
            checks["visible_text_found"] = accname
            return FindingValidationResult(
                verdict="FALSE_POSITIVE",
                reason=f"Button contains visible text {accname!r} — accessible name present.",
                details=checks,
                confidence=0.95,
            )

        # No accessible name found
        checks["no_accessible_name_found"] = True
        return FindingValidationResult(
            verdict="CONFIRMED",
            reason="Button has no accessible name: no text content, aria-label, aria-labelledby, title, or SVG title.",
            details=checks,
            confidence=0.95,
        )

    def _validate_aria_hidden_focus(
        self, element_attrs: dict[str, str]
    ) -> FindingValidationResult:
        """
        Handle ``aria-hidden-focus`` rule.

        An interactive element that is simultaneously focusable and hidden
        from assistive technology is ALWAYS a real bug — keyboard users who
        are also AT users cannot operate the element even though it receives
        focus.  No false-positive scenario exists for this rule.
        """
        return FindingValidationResult(
            verdict="CONFIRMED",
            reason=(
                "An interactive element inside an aria-hidden subtree is always "
                "a genuine violation — AT users cannot reach this element despite "
                "it being in the tab order."
            ),
            details={
                "rule_id": "aria-hidden-focus",
                "checks_run": ["always_confirmed"],
                "aria_hidden": element_attrs.get("aria-hidden", ""),
                "tabindex": element_attrs.get("tabindex", ""),
            },
            confidence=1.0,
        )

    def _validate_region(
        self,
        element_attrs: dict[str, str],
        element_tag: str,
        block_src: str,
    ) -> FindingValidationResult:
        """
        Handle ``region`` landmark rule.

        A finding is FALSE_POSITIVE if:
        - The element already has a ``role`` attribute, OR
        - The element tag is an inherently semantic landmark element:
          ``<main>``, ``<nav>``, ``<aside>``, ``<header>``, ``<footer>``
          (these implicitly map to landmark roles in the accessibility tree).
        - ``<section>`` with ``aria-label`` or ``aria-labelledby`` is also a
          landmark (maps to ``region`` role).

        A bare ``<section>`` without a label does NOT map to a landmark role
        and therefore remains CONFIRMED.
        """
        checks: dict[str, Any] = {
            "rule_id": "region",
            "element_tag": element_tag,
            "checks_run": ["role_attribute", "semantic_landmark_tag"],
        }

        # 1. Explicit role attribute
        existing_role = element_attrs.get("role", "").strip()
        if existing_role:
            checks["role_found"] = existing_role
            return FindingValidationResult(
                verdict="FALSE_POSITIVE",
                reason=f"Element already has role={existing_role!r} — landmark role is defined.",
                details=checks,
                confidence=0.97,
            )

        # 2. Inherently semantic landmark elements (except section without label)
        inherent_landmarks = {"main", "nav", "aside", "header", "footer"}
        if element_tag in inherent_landmarks:
            checks["semantic_landmark_tag"] = element_tag
            return FindingValidationResult(
                verdict="FALSE_POSITIVE",
                reason=(
                    f"<{element_tag}> is a native HTML landmark element — it implicitly "
                    "maps to a landmark role in the accessibility tree."
                ),
                details=checks,
                confidence=0.97,
            )

        # 3. <section> with aria-label or aria-labelledby → maps to 'region' landmark
        if element_tag == "section":
            section_label = element_attrs.get("aria-label", "").strip()
            section_labelledby = element_attrs.get("aria-labelledby", "").strip()
            if section_label:
                checks["section_aria_label"] = section_label
                return FindingValidationResult(
                    verdict="FALSE_POSITIVE",
                    reason=(
                        f"<section aria-label={section_label!r}> maps to the 'region' "
                        "landmark role — the scanner fired incorrectly."
                    ),
                    details=checks,
                    confidence=0.95,
                )
            if section_labelledby:
                checks["section_aria_labelledby"] = section_labelledby
                return FindingValidationResult(
                    verdict="FALSE_POSITIVE",
                    reason=(
                        f"<section aria-labelledby={section_labelledby!r}> maps to the "
                        "'region' landmark role — the scanner fired incorrectly."
                    ),
                    details=checks,
                    confidence=0.95,
                )

        # No landmark found
        checks["no_landmark_found"] = True
        return FindingValidationResult(
            verdict="CONFIRMED",
            reason=(
                "Element is not a native landmark and has no explicit role attribute. "
                "Page content may not be contained within a landmark region."
            ),
            details=checks,
            confidence=0.90,
        )

    def _validate_image_alt(
        self,
        element_attrs: dict[str, str],
        source_context: Any,
    ) -> FindingValidationResult:
        """
        Handle ``image-alt`` rule.

        Outcomes:
        - ``alt=""`` (empty string) + element is decorative context → FALSE_POSITIVE
        - ``alt`` is a generic placeholder (e.g. ``"image"``/``"photo"``) → CONFIRMED
        - ``alt`` contains meaningful text → FALSE_POSITIVE
        - No ``alt`` attribute at all → CONFIRMED
        """
        checks: dict[str, Any] = {
            "rule_id": "image-alt",
            "checks_run": ["alt_presence", "alt_value", "decorative_context"],
        }

        is_decorative: bool = (
            source_context.is_decorative
            if hasattr(source_context, "is_decorative")
            else source_context.get("is_decorative", False)
        )

        # Check for alt attribute existence
        if "alt" not in element_attrs:
            checks["alt_present"] = False
            return FindingValidationResult(
                verdict="CONFIRMED",
                reason="Image has no alt attribute — non-text content is not labelled.",
                details=checks,
                confidence=0.98,
            )

        alt_value: str = element_attrs["alt"]
        checks["alt_present"] = True
        checks["alt_value"] = alt_value

        # Empty alt on a decorative image → correct technique
        if alt_value == "" and is_decorative:
            checks["is_decorative"] = True
            return FindingValidationResult(
                verdict="FALSE_POSITIVE",
                reason=(
                    "Image has alt=\"\" and is flagged as decorative — "
                    "empty alt is the correct technique for decorative images."
                ),
                details=checks,
                confidence=0.92,
            )

        # Generic / meaningless alt text
        if alt_value.strip().lower() in _GENERIC_ALT_TEXT:
            checks["generic_alt"] = True
            return FindingValidationResult(
                verdict="CONFIRMED",
                reason=(
                    f"Image alt={alt_value!r} is a generic placeholder that conveys "
                    "no meaningful information to AT users."
                ),
                details=checks,
                confidence=0.93,
            )

        # Non-empty, non-generic alt text → meaningful label present
        checks["meaningful_alt"] = alt_value
        return FindingValidationResult(
            verdict="FALSE_POSITIVE",
            reason=(
                f"Image has meaningful alt text {alt_value!r} — "
                "the accessible name is provided."
            ),
            details=checks,
            confidence=0.94,
        )

    def _validate_heading_order(
        self,
        element_attrs: dict[str, str],
        element_tag: str,
        block_src: str,
    ) -> FindingValidationResult:
        """
        Handle ``heading-order`` rule.

        Detects when heading levels skip (e.g. ``<h1>`` followed immediately by
        ``<h4>`` with no intervening ``<h2>`` or ``<h3>``).

        Strategy:
        1. Extract the numeric level from ``element_tag`` (e.g. ``'h4'`` → 4).
        2. Scan ``block_src`` for the nearest preceding heading level.
        3. If the skip is > 1 level → CONFIRMED.
        4. If the skip is ≤ 1 or no preceding heading found → PARTIALLY_CONFIRMED
           (cannot be certain without the full document heading map).
        """
        checks: dict[str, Any] = {
            "rule_id": "heading-order",
            "element_tag": element_tag,
            "checks_run": ["heading_level_extraction", "preceding_heading_scan"],
        }

        # Extract current heading level
        h_match = re.match(r"h([1-6])", element_tag, re.IGNORECASE)
        if not h_match:
            checks["heading_tag_not_detected"] = True
            return FindingValidationResult(
                verdict="PARTIALLY_CONFIRMED",
                reason=(
                    "Could not determine heading level from element_tag — "
                    "scanner reported heading-order issue; manual verification needed."
                ),
                details=checks,
                confidence=0.50,
            )

        current_level = int(h_match.group(1))
        checks["current_heading_level"] = current_level

        # Scan block source for preceding heading tags
        preceding_headings = re.findall(
            r"<h([1-6])\b", block_src, re.IGNORECASE
        )
        checks["preceding_headings_in_block"] = preceding_headings

        if preceding_headings:
            # Split the block source on the element's own opening tag so we
            # only inspect headings that appear *before* the current element.
            # This prevents comparing the element to itself (e.g. h4 → h4).
            element_open_tag_pattern = rf"<{re.escape(element_tag)}\b"
            split_parts = re.split(
                element_open_tag_pattern, block_src, maxsplit=1, flags=re.IGNORECASE
            )
            before_element = split_parts[0] if len(split_parts) > 1 else ""

            preceding_before = re.findall(r"<h([1-6])\b", before_element, re.IGNORECASE)
            checks["preceding_headings_before_element"] = preceding_before

            if preceding_before:
                last_preceding = int(preceding_before[-1])
                checks["last_preceding_heading_level"] = last_preceding
                skip = current_level - last_preceding

                if skip > 1:
                    checks["level_skip"] = skip
                    return FindingValidationResult(
                        verdict="CONFIRMED",
                        reason=(
                            f"Heading level skips from h{last_preceding} to h{current_level} "
                            f"(skip of {skip} levels) — violates heading hierarchy."
                        ),
                        details=checks,
                        confidence=0.90,
                    )
                # Skip of 0 or 1 — scanner may have fired for other reason
                return FindingValidationResult(
                    verdict="PARTIALLY_CONFIRMED",
                    reason=(
                        f"Heading level transition h{last_preceding}→h{current_level} in "
                        "block source does not show a skip, but full document heading map "
                        "is needed to rule out the violation entirely."
                    ),
                    details=checks,
                    confidence=0.65,
                )
            # Headings found in full block but none strictly before this element

        # No preceding headings in block — cannot determine from local context
        return FindingValidationResult(
            verdict="NOT_ENOUGH_EVIDENCE",
            reason=(
                "No preceding headings found in the source block — cannot determine "
                "if a heading level was skipped without the full document context."
            ),
            details=checks,
            confidence=0.40,
        )

    # ------------------------------------------------------------------
    # Heuristics (private)
    # ------------------------------------------------------------------

    def _is_accordion_or_disclosure(
        self,
        element_attrs: dict[str, str],
        element_tag: str,
        block_src: str,
    ) -> bool:
        """
        Return ``True`` if the element looks like an accordion or custom
        disclosure widget that requires behavioural analysis.

        Triggers when ALL of the following are true:
        - ``element_tag`` is ``div``, ``span``, ``li``, or ``button``
        - ``aria-expanded`` is present in attributes OR onclick in attributes/block
        """
        disclosure_tags = {"div", "span", "li", "button"}
        if element_tag not in disclosure_tags:
            return False

        has_aria_expanded = "aria-expanded" in element_attrs
        has_onclick = bool(element_attrs.get("onclick")) or "onclick" in block_src
        return has_aria_expanded or has_onclick

    # ------------------------------------------------------------------
    # Accessible Name Computation (static helper)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_accname(
        element_attrs: dict[str, str],
        block_source: str,
        element_tag: str,
    ) -> str:
        """
        Approximate the accessible name for an element using standard
        accessible name computation order (ARIA spec §4.3).

        Checks (in priority order):
        1. ``aria-label`` attribute — returned directly if non-empty.
        2. ``aria-labelledby`` — referenced element IDs searched in
           ``block_source`` for their text content.
        3. ``title`` attribute — returned directly if non-empty.
        4. Visible text content extracted from the HTML block for the element.

        Parameters
        ----------
        element_attrs:
            Parsed attribute dict for the element.
        block_source:
            Surrounding HTML block as a raw string.
        element_tag:
            Lower-cased HTML tag name (used to scope text extraction).

        Returns
        -------
        str
            The computed accessible name, or empty string if none is found.

        Notes
        -----
        This is a *best-effort* static approximation.  It does not execute
        JavaScript or resolve dynamic references.  For definitive accessible
        name computation, a live browser accessibility tree is required.
        """
        # 1. aria-label
        aria_label = element_attrs.get("aria-label", "").strip()
        if aria_label:
            return aria_label

        # 2. aria-labelledby — concatenate text of referenced elements
        aria_labelledby = element_attrs.get("aria-labelledby", "").strip()
        if aria_labelledby:
            id_texts: list[str] = []
            for ref_id in aria_labelledby.split():
                # Look for the element with matching id and extract its text
                id_pattern = rf"""id\s*=\s*["']?{re.escape(ref_id)}["']?[^>]*>([^<]*)"""
                m = re.search(id_pattern, block_source, re.IGNORECASE)
                if m:
                    extracted = m.group(1).strip()
                    if extracted:
                        id_texts.append(extracted)
            if id_texts:
                return " ".join(id_texts)

        # 3. title attribute
        title = element_attrs.get("title", "").strip()
        if title:
            return title

        # 4. Visible text content of the element from block_source
        if block_source and element_tag:
            # Extract content between opening and closing tags of the element
            tag_pattern = rf"<{re.escape(element_tag)}\b[^>]*>(.*?)</{re.escape(element_tag)}>"
            matches = re.findall(tag_pattern, block_source, re.IGNORECASE | re.DOTALL)
            for inner in matches:
                # Strip nested tags, collapse whitespace
                text = re.sub(r"<[^>]+>", " ", inner)
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    return text

        return ""
