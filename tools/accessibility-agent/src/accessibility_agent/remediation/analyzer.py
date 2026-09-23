"""
Source Analyzer — deep-reads source code around a SourceLocation match.

Given a SourceLocation from Phase 1, the SourceAnalyzer:
  1. Reads the full block of source code (matched line ± 15 lines)
  2. Parses the HTML tag and all its attributes
  3. Detects structural context: is it inside a form? An icon-only button?
  4. Detects framework-specific patterns: event handlers, CSS-in-JS class names
  5. Extracts nearby labels and ARIA associations
  6. Makes a preliminary problem_type classification (refined by RemediationClassifier)
  7. Packages everything into a SourceContext for the planner

This module is DETERMINISTIC — no LLM calls.  The LLM gets the SourceContext
as input; it does not re-read the file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from accessibility_agent.logging_config import get_logger
from accessibility_agent.remediation.schemas import (
    ApplicationFramework,
    ProblemType,
    SourceContext,
    SourceLocation,
)

log = get_logger(__name__)

# How many lines of context to read around the match
_CONTEXT_RADIUS = 15

# Common generic accessible names that indicate an accessibility problem
_GENERIC_TEXT = {
    "click here", "click", "here", "more", "read more", "learn more",
    "see more", "view more", "go", "submit", "ok", "continue",
    "next", "back", "previous", "close", "open", "menu", "link",
}


class SourceAnalyzer:
    """
    Analyzes the source code at a SourceLocation to build a SourceContext.

    Usage:
        analyzer = SourceAnalyzer(repo_path=Path("./my-app"))
        context = analyzer.analyze(location, finding_data)
    """

    def __init__(self, repo_path: Path) -> None:
        self._repo = repo_path.resolve()

    def analyze(
        self,
        location: SourceLocation,
        finding_data: dict[str, Any],
    ) -> SourceContext:
        """
        Read the source file at the given location and return a SourceContext.

        Args:
            location:     SourceLocation from Phase 1 (locator output)
            finding_data: The original Finding dict (from the scan report)

        Returns:
            SourceContext with all structural analysis populated.
        """
        if location.confidence.value == "not_found" or not location.file_path:
            log.warning("source_analyzer.location_not_found", finding_id=finding_data.get("finding_id"))
            return self._empty_context(location)

        abs_path = self._repo / location.file_path
        if not abs_path.exists():
            log.warning("source_analyzer.file_missing", path=str(abs_path))
            return self._empty_context(location)

        try:
            all_lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            log.error("source_analyzer.read_error", path=str(abs_path), error=str(exc))
            return self._empty_context(location)

        # Centre on the best matched line (start_line is the region start, use midpoint)
        centre_idx = location.start_line - 1  # 0-indexed
        block_start = max(0, centre_idx - _CONTEXT_RADIUS)
        block_end = min(len(all_lines), centre_idx + _CONTEXT_RADIUS + 1)

        block_lines = all_lines[block_start:block_end]
        block_source = "\n".join(
            f"{block_start + i + 1}: {ln}" for i, ln in enumerate(block_lines)
        )

        # The exact matched line
        matched_line = all_lines[centre_idx].strip() if centre_idx < len(all_lines) else ""

        log.info(
            "source_analyzer.analyzing",
            file=location.file_path,
            line=centre_idx + 1,
            block_lines=len(block_lines),
        )

        # ── Determine which HTML tag to expect ────────────────────────────────
        # Derive the expected tag from the finding data so the block scanner
        # knows what to look for — not just "the first tag in the block".
        expected_tag = self._expected_tag_from_finding(finding_data)

        # ── Parse element attributes ──────────────────────────────────────────
        # Prefer matched_text from the locator (the exact line that was matched).
        tag_source_line = location.matched_text.strip() or matched_line

        # If the candidate line has no tag (blank, comment, or wrong element),
        # search the block for the first line that contains the expected tag.
        if not self._extract_tag(tag_source_line) or (
            expected_tag and self._extract_tag(tag_source_line) != expected_tag
        ):
            for bl in block_lines:
                tag_in_line = self._extract_tag(bl)
                if expected_tag:
                    if tag_in_line == expected_tag:
                        tag_source_line = bl.strip()
                        break
                elif tag_in_line:
                    tag_source_line = bl.strip()
                    break

        element_tag = self._extract_tag(tag_source_line)
        element_attrs = self._extract_attributes(tag_source_line)

        # ── Structural analysis ───────────────────────────────────────────────
        block_text = "\n".join(block_lines)
        is_inside_form = self._is_inside_form(block_text)
        is_icon_only = self._is_icon_only(block_text, element_tag)
        is_decorative = self._is_decorative(element_tag, element_attrs, finding_data)
        has_handlers, handler_names = self._detect_event_handlers(matched_line, block_text, location.framework)
        nearby_labels = self._find_nearby_labels(block_text, element_attrs)
        sibling_elements = self._find_sibling_elements(block_lines, centre_idx - block_start, element_tag)
        parent_element = self._find_parent_element(block_lines, centre_idx - block_start)
        component_name = self._infer_component_name(location.file_path)

        # ── Problem type classification ────────────────────────────────────────
        problem_type, problem_summary = self._classify_problem(
            element_tag, element_attrs, finding_data, block_text, location.language
        )

        context = SourceContext(
            file_path=location.file_path,
            start_line=location.start_line,
            end_line=location.end_line,
            language=location.language,
            framework=location.framework,
            matched_element_line=matched_line,
            block_source=block_source,
            element_tag=element_tag,
            element_attributes=element_attrs,
            parent_element=parent_element,
            sibling_elements=sibling_elements,
            nearby_labels=nearby_labels,
            component_name=component_name,
            has_event_handlers=has_handlers,
            event_handler_names=handler_names,
            is_inside_form=is_inside_form,
            is_icon_only=is_icon_only,
            is_decorative=is_decorative,
            problem_type=problem_type,
            problem_summary=problem_summary,
        )

        log.info(
            "source_analyzer.complete",
            file=location.file_path,
            tag=element_tag,
            problem_type=problem_type.value,
            is_icon_only=is_icon_only,
            is_inside_form=is_inside_form,
        )

        return context

    # ── Tag and attribute parsing ─────────────────────────────────────────────

    @staticmethod
    def _extract_tag(line: str) -> str:
        """Extract the HTML/JSX tag name from a source line."""
        # Matches: <button, <Button, <input, <img, <a, <html etc.
        m = re.search(r"<([a-zA-Z][a-zA-Z0-9]*)", line)
        return m.group(1).lower() if m else ""

    @staticmethod
    def _extract_attributes(line: str) -> dict[str, str]:
        """
        Parse HTML/JSX attributes from a source line into a dict.
        Handles: attr="value", attr='value', attr={expr}, boolean attrs.
        """
        attrs: dict[str, str] = {}
        # Double-quoted: attr="value"
        for m in re.finditer(r'([\w-]+)\s*=\s*"([^"]*)"', line):
            attrs[m.group(1).lower()] = m.group(2)
        # Single-quoted: attr='value'
        for m in re.finditer(r"([\w-]+)\s*=\s*'([^']*)'", line):
            if m.group(1).lower() not in attrs:
                attrs[m.group(1).lower()] = m.group(2)
        # JSX expression: attr={value} — capture as placeholder
        for m in re.finditer(r"([\w-]+)\s*=\s*\{([^}]{0,60})\}", line):
            if m.group(1).lower() not in attrs:
                attrs[m.group(1).lower()] = f"{{{m.group(2)}}}"
        # Boolean attrs (e.g. disabled, required, checked)
        for m in re.finditer(r"\b(disabled|required|checked|readonly|hidden|multiple|autoFocus)\b", line):
            attr = m.group(1).lower()
            if attr not in attrs:
                attrs[attr] = "true"
        return attrs

    # ── Structural detectors ──────────────────────────────────────────────────

    @staticmethod
    def _is_inside_form(block_text: str) -> bool:
        """Heuristic: check if any <form tag appears before the element in the block."""
        form_pos = block_text.lower().find("<form")
        close_form_pos = block_text.lower().find("</form>")
        if form_pos == -1:
            return False
        # If we see a <form before </form> or no closing form, we're inside one
        if close_form_pos == -1 or form_pos < close_form_pos:
            return True
        return False

    @staticmethod
    def _is_icon_only(block_text: str, element_tag: str) -> bool:
        """
        True if the element appears to contain only an icon/SVG/img with no text.

        Common patterns:
        - <button><img .../></button>
        - <button><svg ...></svg></button>
        - <button><span class="icon-..."></span></button>

        NOTE: Only applies to interactive elements (button, a).
        A plain <div> with an SVG inside is NOT considered icon-only.
        """
        if element_tag not in ("button", "a"):
            return False
        # Look for SVG or img-only children with no text
        has_svg = bool(re.search(r"<svg\b", block_text, re.IGNORECASE))
        has_img = bool(re.search(r"<img\b", block_text, re.IGNORECASE))
        has_icon_class = bool(re.search(r'class[=Name]*["\'][^"\']*(?:icon|fa-|bi-|material-)[^"\']*["\']', block_text, re.IGNORECASE))
        # Check if there's meaningful text content
        stripped = re.sub(r"<[^>]+>", "", block_text).strip()
        has_text = bool(stripped) and len(stripped) > 1
        return (has_svg or has_img or has_icon_class) and not has_text

    @staticmethod
    def _is_decorative(
        element_tag: str,
        attrs: dict[str, str],
        finding_data: dict[str, Any],
    ) -> bool:
        """
        True if the element is likely decorative (no functional role).

        An image inside a button that has text → decorative (alt="" is correct).
        A standalone img with role="img" → informational (alt required).
        """
        if element_tag != "img":
            return False
        # If aria-hidden or role=presentation → explicitly decorative
        if attrs.get("aria-hidden") == "true" or attrs.get("role") == "presentation":
            return True
        # If already has alt="" → already marked decorative
        if attrs.get("alt") == "":
            return True
        return False

    @staticmethod
    def _detect_event_handlers(
        line: str, block_text: str, framework: ApplicationFramework
    ) -> tuple[bool, list[str]]:
        """Detect event handlers on or near the element."""
        handlers: list[str] = []
        patterns = [
            r"on[A-Z][a-zA-Z]+\s*=",      # JSX: onClick=, onKeyDown=
            r"v-on:[a-z]+",                # Vue: v-on:click
            r"@[a-z]+\s*=",               # Vue shorthand: @click=
            r"ng-click|ng-change",         # Angular (legacy)
            r"\(click\)|\(change\)",       # Angular
            r"onclick|onkeydown|onchange", # Plain HTML
        ]
        # Search both the matched line and the full block (for multi-line JSX)
        search_text = block_text if block_text else line
        for p in patterns:
            for m in re.finditer(p, search_text, re.IGNORECASE):
                handlers.append(m.group(0).rstrip("=").strip())
        return bool(handlers), list(set(handlers))

    @staticmethod
    def _expected_tag_from_finding(finding_data: dict[str, Any]) -> str:
        """
        Derive the expected HTML element tag from the finding data.

        This is used as a hint for the block scanner so it looks for
        the right tag (e.g. 'button') and not the first tag it encounters
        (which might be 'main' or 'div').

        Priority:
          1. element.html (parse it directly)
          2. element.role → map to tag
          3. rule_id → known tag mapping
        """
        element = finding_data.get("element", {})

        # From element.html — most reliable
        html_snippet = element.get("html", "")
        if html_snippet:
            m = re.search(r"<([a-zA-Z][a-zA-Z0-9]*)", html_snippet)
            if m:
                return m.group(1).lower()

        # From rule_id — known mappings
        rule_id = finding_data.get("rule_id", "")
        _RULE_TO_TAG = {
            "button-name": "button",
            "input-button-name": "input",
            "label": "input",
            "image-alt": "img",
            "html-has-lang": "html",
            "html-lang-valid": "html",
            "document-title": "title",
            "link-name": "a",
            "tabindex": None,  # Could be any element
        }
        for rule_prefix, tag in _RULE_TO_TAG.items():
            if rule_prefix in rule_id and tag:
                return tag

        # From role → tag
        role = element.get("role", "")
        _ROLE_TO_TAG = {
            "button": "button",
            "link": "a",
            "img": "img",
            "textbox": "input",
            "checkbox": "input",
            "radio": "input",
            "combobox": "select",
        }
        return _ROLE_TO_TAG.get(role, "")

    @staticmethod
    def _find_nearby_labels(block_text: str, attrs: dict[str, str]) -> list[str]:
        """Find label elements or aria-label values near the element."""
        labels: list[str] = []
        # aria-label directly on element
        if "aria-label" in attrs:
            labels.append(f'aria-label="{attrs["aria-label"]}"')
        # aria-labelledby reference
        if "aria-labelledby" in attrs:
            labels.append(f'aria-labelledby="{attrs["aria-labelledby"]}"')
        # <label> elements in the block
        for m in re.finditer(r'<label[^>]*>([^<]*)</label>', block_text, re.IGNORECASE):
            text = m.group(1).strip()
            if text:
                labels.append(f"<label>{text}</label>")
        return labels

    @staticmethod
    def _find_sibling_elements(
        lines: list[str], match_idx: int, element_tag: str
    ) -> list[str]:
        """Extract up to 4 surrounding sibling element tags for context."""
        siblings: list[str] = []
        for i, ln in enumerate(lines):
            if i == match_idx:
                continue
            m = re.search(r"<([a-zA-Z][a-zA-Z0-9]*)", ln)
            if m and m.group(1).lower() != element_tag:
                sibling_tag = m.group(1).lower()
                if sibling_tag not in {"div", "span", "section", "fragment"} and len(siblings) < 4:
                    siblings.append(sibling_tag)
        return siblings

    @staticmethod
    def _find_parent_element(lines: list[str], match_idx: int) -> str:
        """Walk backwards through lines to find the nearest opening parent tag."""
        for i in range(match_idx - 1, -1, -1):
            m = re.search(r"<([a-zA-Z][a-zA-Z0-9]*)[^/]*(?<!/)>", lines[i])
            if m:
                return m.group(1).lower()
        return ""

    @staticmethod
    def _infer_component_name(file_path: str) -> str:
        """Extract the component name from the file path."""
        name = Path(file_path).stem
        # Remove common suffixes: .component, .page, .view, .container
        name = re.sub(r"\.(component|page|view|container|module)$", "", name)
        return name

    # ── Problem type classification ───────────────────────────────────────────

    def _classify_problem(
        self,
        element_tag: str,
        attrs: dict[str, str],
        finding_data: dict[str, Any],
        block_text: str,
        language: str,
    ) -> tuple[ProblemType, str]:
        """
        Classify the problem type from structural evidence alone (no LLM).
        Returns (ProblemType, problem_summary).
        """
        rule_id = finding_data.get("rule_id", "")
        wcag_sc = finding_data.get("wcag", {}).get("success_criterion", "")
        description = finding_data.get("description", "").lower()
        element_html = finding_data.get("element", {}).get("html", "").lower()
        text_content = finding_data.get("element", {}).get("text_content", "").strip().lower()

        # ── HTML lang attribute ───────────────────────────────────────────────
        if "html-has-lang" in rule_id or "html-lang" in rule_id or (
            element_tag == "html" and "lang" not in attrs
        ):
            return ProblemType.MISSING_MARKUP, "The <html> element is missing the lang attribute."

        # ── Document title ────────────────────────────────────────────────────
        if "document-title" in rule_id or "2.4.2" in wcag_sc:
            return ProblemType.MISSING_MARKUP, "The page is missing a <title> element."

        # ── Image alt text ────────────────────────────────────────────────────
        if element_tag == "img" or "image-alt" in rule_id or "1.1.1" in wcag_sc:
            if "alt" not in attrs:
                return ProblemType.IMAGE_ALT, "Image is missing an alt attribute entirely."
            if attrs.get("alt", "x") == "" and self._is_icon_only(block_text, element_tag):
                return ProblemType.IMAGE_ALT, "Image has empty alt inside a non-decorative context."
            return ProblemType.IMAGE_ALT, "Image alt text needs review."

        # ── Form input labeling ───────────────────────────────────────────────
        if element_tag in ("input", "select", "textarea") or "label" in rule_id:
            return ProblemType.FORM_LABELING, f"The <{element_tag}> element has no associated label."

        # ── Button accessible name ────────────────────────────────────────────
        if element_tag == "button" or "button-name" in rule_id:
            return ProblemType.INCORRECT_ARIA, "Button has no accessible name (no text, no aria-label, no aria-labelledby)."

        # ── Link text ─────────────────────────────────────────────────────────
        if (element_tag == "a" or "link" in rule_id) and text_content in _GENERIC_TEXT:
            return ProblemType.LINK_TEXT, f"Link text '{text_content}' is not descriptive."

        # ── Focus visibility ──────────────────────────────────────────────────
        if "focus" in description or "outline:none" in element_html or language in ("css", "scss"):
            return ProblemType.FOCUS_VISIBILITY, "Focus indicator is hidden or removed."

        # ── Color contrast ────────────────────────────────────────────────────
        if "contrast" in rule_id or "1.4.3" in wcag_sc or "1.4.11" in wcag_sc:
            return ProblemType.COLOR_CONTRAST, "Color contrast ratio does not meet WCAG threshold."

        # ── aria-hidden on interactive ────────────────────────────────────────
        if attrs.get("aria-hidden") == "true" and element_tag in ("button", "a", "input", "select"):
            return ProblemType.INCORRECT_ARIA, f"aria-hidden='true' on interactive <{element_tag}> hides it from assistive technology."

        # ── Positive tabindex ─────────────────────────────────────────────────
        tabindex = attrs.get("tabindex", "")
        try:
            if int(tabindex) > 0:
                return ProblemType.KEYBOARD_ACCESS, f"Positive tabindex={tabindex} disrupts natural tab order."
        except (ValueError, TypeError):
            pass

        # ── Semantic structure ────────────────────────────────────────────────
        if element_tag in ("h1", "h2", "h3", "h4", "h5", "h6") or "heading" in rule_id:
            return ProblemType.SEMANTIC_STRUCTURE, "Heading structure is incorrect."

        # ── Landmark / role ───────────────────────────────────────────────────
        if "landmark" in rule_id or "region" in rule_id:
            return ProblemType.SEMANTIC_STRUCTURE, "Page landmark structure is missing or incorrect."

        return ProblemType.UNKNOWN, description[:150] if description else "Unknown accessibility problem."

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _empty_context(location: SourceLocation) -> SourceContext:
        """Return an empty SourceContext when analysis is not possible."""
        return SourceContext(
            file_path=location.file_path,
            start_line=location.start_line,
            end_line=location.end_line,
            language=location.language,
            framework=location.framework,
            problem_type=ProblemType.UNKNOWN,
            problem_summary="Source analysis could not be performed.",
        )
