"""
remediation/widget_classifier.py — Stage 6 of the Remediation Pipeline.

Identifies the ARIA design pattern / widget type for a given accessibility
finding so that the Remediation Planner knows:

1. Whether a simple attribute patch is sufficient (e.g. add ``alt="…"``), or
2. Whether a full interaction-model review is required (e.g. a tablist that
   must implement all ARIA keyboard conventions).

This classification is purely deterministic — no LLM is involved.  The result
drives two downstream decisions:

- **Remediation strategy** — which code change template to apply.
- **Automation safety gate** — widgets in ``WIDGET_REQUIRES_MANUAL_REVIEW``
  are automatically routed to the MANUAL_REVIEW_REQUIRED path unless a
  higher-level policy explicitly overrides.

Integration
-----------
``WidgetClassifier.classify()`` is called after ``FindingValidator`` confirms
or partially-confirms a finding, and before ``RemediationPlanner`` produces a
``RemediationPlan``.  The returned ``WidgetPattern`` is stored in the audit
trace and used to select the correct planning prompt / code template.

Usage example::

    from accessibility_agent.remediation.widget_classifier import (
        WidgetClassifier,
        WidgetPattern,
        WIDGET_REQUIRES_MANUAL_REVIEW,
    )

    classifier = WidgetClassifier()
    pattern = classifier.classify(element_tag, element_attrs, block_source, rule_id)

    if pattern in WIDGET_REQUIRES_MANUAL_REVIEW:
        # Escalate to manual review package
        ...
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any


# ── Widget Pattern Enumeration ────────────────────────────────────────────────


class WidgetPattern(Enum):
    """
    ARIA design pattern / widget classification for an accessibility finding.

    Each value maps to a distinct remediation strategy.  The string value is
    used in audit traces and API responses.

    Attributes
    ----------
    UNKNOWN:
        Could not be classified.  Treated as needing human review.
    SIMPLE_ATTRIBUTE:
        Fix requires adding or correcting a single HTML/ARIA attribute.
        Examples: ``lang``, ``alt``, ``title``, ``aria-hidden`` removal.
    INTERACTIVE_BUTTON:
        Native ``<button>`` needing an accessible name.
    INTERACTIVE_LINK:
        Native ``<a>`` needing descriptive text or ``aria-label``.
    FORM_FIELD:
        ``<input>``, ``<select>``, or ``<textarea>`` needing a label.
    ACCORDION:
        Disclosure / accordion widget.  Requires reviewing expanded-state
        management, keyboard conventions, and ARIA attributes together.
    DIALOG:
        Modal or non-modal dialog.  Requires focus management, ``aria-modal``,
        ``aria-labelledby``, and Escape-key handling review.
    TABS:
        Tablist / tab / tabpanel pattern.  Requires review of roving tabindex
        and arrow-key navigation.
    MENU:
        Menu, menubar, or menuitem pattern.  Requires review of keyboard
        navigation (arrow keys, Escape, Enter/Space).
    COMBOBOX:
        Combobox pattern (input + listbox).
    LISTBOX:
        Listbox (``role="listbox"`` with ``option`` children).
    CAROUSEL:
        Carousel / slider.  Requires auto-play pause, prev/next controls
        review, and live-region consideration.
    CUSTOM_INTERACTIVE:
        ``<div>`` or ``<span>`` with ``onclick`` — custom widget that does not
        use a native interactive element.  Requires full ARIA role, keyboard,
        and focus-management review.
    LANDMARK:
        Missing or incorrect landmark region (``<main>``, ``<nav>``, etc.).
    IMAGE:
        ``<img>`` needing an ``alt`` attribute or role adjustment.
    HEADING:
        Heading hierarchy or structure issue.
    TABLE:
        Table accessibility issue (missing headers, caption, scope, etc.).
    IFRAME:
        ``<iframe>`` needing a ``title`` attribute.
    FORM_GROUP:
        ``<fieldset>`` / ``<legend>`` grouping issue.
    """

    UNKNOWN = "unknown"
    SIMPLE_ATTRIBUTE = "simple_attribute"
    INTERACTIVE_BUTTON = "interactive_button"
    INTERACTIVE_LINK = "interactive_link"
    FORM_FIELD = "form_field"
    ACCORDION = "accordion"
    DIALOG = "dialog"
    TABS = "tabs"
    MENU = "menu"
    COMBOBOX = "combobox"
    LISTBOX = "listbox"
    CAROUSEL = "carousel"
    CUSTOM_INTERACTIVE = "custom_interactive"
    LANDMARK = "landmark"
    IMAGE = "image"
    HEADING = "heading"
    TABLE = "table"
    IFRAME = "iframe"
    FORM_GROUP = "form_group"


# ── Manual Review Guard Set ───────────────────────────────────────────────────

WIDGET_REQUIRES_MANUAL_REVIEW: frozenset[WidgetPattern] = frozenset(
    {
        WidgetPattern.ACCORDION,
        WidgetPattern.DIALOG,
        WidgetPattern.TABS,
        WidgetPattern.MENU,
        WidgetPattern.COMBOBOX,
        WidgetPattern.LISTBOX,
        WidgetPattern.CAROUSEL,
        WidgetPattern.CUSTOM_INTERACTIVE,
    }
)
"""
Set of ``WidgetPattern`` values that are automatically routed to
``MANUAL_REVIEW_REQUIRED`` in the remediation pipeline.

Rationale: These patterns involve complex interaction models (keyboard
conventions, state management, live regions, focus trapping) that cannot
be correctly implemented by generating a simple attribute patch.  A human
must design the complete accessible interaction before any code is written.
"""


# ── Internal Lookup Tables ────────────────────────────────────────────────────

#: Rules that always indicate a simple attribute problem, regardless of element.
_SIMPLE_ATTRIBUTE_RULES: frozenset[str] = frozenset(
    {
        "aria-hidden-focus",
        "aria-hidden-body",
        "html-has-lang",
        "html-lang-valid",
        "document-title",
        "meta-viewport",
        "scrollable-region-focusable",
        "bypass",
        "landmark-one-main",
    }
)

#: Role attribute values that indicate ARIA dialog pattern.
_DIALOG_ROLES: frozenset[str] = frozenset({"dialog", "alertdialog"})

#: Role attribute values that indicate tab pattern.
_TAB_ROLES: frozenset[str] = frozenset({"tablist", "tab", "tabpanel"})

#: Role attribute values that indicate menu pattern.
_MENU_ROLES: frozenset[str] = frozenset({"menu", "menubar", "menuitem", "menuitemcheckbox", "menuitemradio"})

#: Role attribute values that indicate combobox.
_COMBOBOX_ROLES: frozenset[str] = frozenset({"combobox"})

#: Role attribute values that indicate listbox.
_LISTBOX_ROLES: frozenset[str] = frozenset({"listbox", "option"})


# ── Classifier ────────────────────────────────────────────────────────────────


class WidgetClassifier:
    """
    Deterministic ARIA widget pattern classifier.

    Instantiate once and reuse — the class holds no mutable state.

    The detection logic follows this priority order:

    1. Rule-ID shortcuts (e.g. ``aria-hidden-focus`` → SIMPLE_ATTRIBUTE)
    2. Explicit ARIA ``role`` attribute on the element
    3. Native HTML tag semantics
    4. Behavioural signals (``aria-expanded``, ``onclick`` in block)
    5. Default → UNKNOWN

    Example
    -------
    ::

        classifier = WidgetClassifier()
        pattern = classifier.classify("div", {"aria-expanded": "false", "onclick": "..."}, block, "button-name")
        # → WidgetPattern.ACCORDION
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(
        self,
        element_tag: str,
        element_attrs: dict[str, str],
        block_source: str,
        rule_id: str,
    ) -> WidgetPattern:
        """
        Classify the widget pattern for an accessibility finding.

        Parameters
        ----------
        element_tag:
            Lower-cased HTML tag of the problematic element, e.g.
            ``'button'``, ``'div'``, ``'input'``, ``'h2'``.
        element_attrs:
            Parsed attribute dict, e.g.
            ``{'role': 'tab', 'aria-selected': 'true'}``.
        block_source:
            Surrounding HTML source block (±15 lines around the element).
            Used to detect ``onclick`` handlers and ARIA roles that may
            appear on ancestor/descendant elements.
        rule_id:
            Lower-cased axe-core or internal rule identifier that produced
            the finding.  Used for rule-level shortcuts.

        Returns
        -------
        WidgetPattern
            The detected pattern.
        """
        tag = (element_tag or "").lower().strip()
        rule = (rule_id or "").lower().strip()
        role = element_attrs.get("role", "").lower().strip()

        # ── 1. Rule-ID shortcuts ──────────────────────────────────────
        if rule in _SIMPLE_ATTRIBUTE_RULES:
            return WidgetPattern.SIMPLE_ATTRIBUTE

        # ── 2. Explicit ARIA role on element ──────────────────────────
        if role in _DIALOG_ROLES:
            return WidgetPattern.DIALOG

        if role in _TAB_ROLES:
            return WidgetPattern.TABS

        if role in _MENU_ROLES:
            return WidgetPattern.MENU

        if role in _COMBOBOX_ROLES:
            return WidgetPattern.COMBOBOX

        if role in _LISTBOX_ROLES:
            return WidgetPattern.LISTBOX

        if role in ("button",):
            return WidgetPattern.INTERACTIVE_BUTTON

        if role in ("link",):
            return WidgetPattern.INTERACTIVE_LINK

        if role in ("img", "figure"):
            return WidgetPattern.IMAGE

        if role in ("region", "main", "navigation", "complementary", "banner", "contentinfo", "form", "search"):
            return WidgetPattern.LANDMARK

        if role in ("heading",):
            return WidgetPattern.HEADING

        if role in ("grid", "table", "row", "columnheader", "rowheader", "cell", "gridcell"):
            return WidgetPattern.TABLE

        if role in ("group",):
            return WidgetPattern.FORM_GROUP

        # ── 3. html/head — document-level attributes only ─────────────
        if tag in ("html", "head"):
            return WidgetPattern.SIMPLE_ATTRIBUTE

        # ── 4. Native tag semantics ───────────────────────────────────
        if tag == "button":
            return WidgetPattern.INTERACTIVE_BUTTON

        if tag == "a":
            return WidgetPattern.INTERACTIVE_LINK

        if tag in ("input", "select", "textarea"):
            return WidgetPattern.FORM_FIELD

        if tag == "img":
            return WidgetPattern.IMAGE

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            return WidgetPattern.HEADING

        if tag == "iframe":
            return WidgetPattern.IFRAME

        if tag in ("table", "th", "td", "tr", "thead", "tbody", "tfoot", "caption"):
            return WidgetPattern.TABLE

        if tag in ("fieldset", "legend"):
            return WidgetPattern.FORM_GROUP

        if tag in ("main", "nav", "aside", "header", "footer"):
            return WidgetPattern.LANDMARK

        if tag == "section":
            # section only maps to landmark when labeled
            has_section_label = bool(
                element_attrs.get("aria-label", "").strip()
                or element_attrs.get("aria-labelledby", "").strip()
            )
            return WidgetPattern.LANDMARK if has_section_label else WidgetPattern.UNKNOWN

        # ── 5. Behavioural signals ────────────────────────────────────

        # Accordion / disclosure: div, span, li, or button with aria-expanded
        # or onclick — note: button is already handled above; keep for completeness
        if "aria-expanded" in element_attrs and tag in ("div", "span", "li", "button"):
            return WidgetPattern.ACCORDION

        # Dialog pattern detected via block scan (role="dialog" on ancestor)
        if self._block_has_role(block_source, _DIALOG_ROLES):
            return WidgetPattern.DIALOG

        # Tab pattern via block
        if self._block_has_role(block_source, _TAB_ROLES):
            return WidgetPattern.TABS

        # Menu pattern via block
        if self._block_has_role(block_source, _MENU_ROLES):
            return WidgetPattern.MENU

        # Custom interactive: div or span with onclick
        if tag in ("div", "span") and (
            element_attrs.get("onclick")
            or "onclick" in block_source
        ):
            return WidgetPattern.CUSTOM_INTERACTIVE

        # Carousel heuristic: look for common carousel indicators in block
        if self._block_looks_like_carousel(block_source, element_attrs, tag):
            return WidgetPattern.CAROUSEL

        # ── 6. Default ────────────────────────────────────────────────
        return WidgetPattern.UNKNOWN

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _block_has_role(block_source: str, roles: frozenset[str]) -> bool:
        """
        Return ``True`` if any ``role="<value>"`` in ``roles`` appears
        anywhere in the surrounding block source.

        Used to detect widgets where the ARIA role is on an ancestor or
        container element rather than directly on the flagged element.
        """
        if not block_source:
            return False
        for role_value in roles:
            if re.search(
                rf"""role\s*=\s*["']?{re.escape(role_value)}["']?""",
                block_source,
                re.IGNORECASE,
            ):
                return True
        return False

    @staticmethod
    def _block_looks_like_carousel(
        block_source: str,
        element_attrs: dict[str, str],
        element_tag: str,
    ) -> bool:
        """
        Heuristically detect carousel / slider widgets in the block source.

        Indicators:
        - ``role="group"`` with ``aria-roledescription="slide"`` / ``"carousel"``
        - CSS classes containing ``carousel``, ``slider``, ``swiper``
        - Data attributes like ``data-slide``, ``data-carousel``
        """
        combined = block_source + " ".join(element_attrs.values())
        carousel_patterns = [
            r"aria-roledescription\s*=\s*[\"']?(slide|carousel)",
            r'class\s*=\s*["\'][^"\']*\b(carousel|slider|swiper|glide|splide)\b',
            r"data-(slide|carousel|swiper)",
        ]
        return any(
            re.search(pat, combined, re.IGNORECASE) for pat in carousel_patterns
        )
