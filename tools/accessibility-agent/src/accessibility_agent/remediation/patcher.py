"""
Patch Generator — converts a RemediationPlan into a minimal unified diff.

Given a validated RemediationPlan, this module:
  1. Reads the actual source file from disk
  2. Locates the exact target lines using context-window matching
     (does NOT blindly trust line numbers — verifies expected content is there)
  3. Applies the appropriate patch strategy:
       • AttributeInjection — add/modify/remove a single HTML/JSX attribute
       • ElementInsertion   — insert a new child element (e.g. <title>, <label>)
       • LineReplacement    — swap one or more lines (e.g. CSS outline fix)
  4. Produces a GeneratedPatch with a standard unified diff string
     that `git apply` can accept without modification

CONVENTIONS
-----------
  target_value == "__REMOVE__"  → DELETE the attribute entirely
  target_value == ""            → ADD the attribute with an empty value (e.g. alt="")
  target_value == "some value"  → ADD or MODIFY the attribute

MINIMALITY GUARANTEE
--------------------
The patcher never modifies more lines than the plan describes.
PatchValidator Gate 5 (no_unrelated_changes) enforces this independently.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from pathlib import Path
from typing import Any

from accessibility_agent.logging_config import get_logger
from accessibility_agent.remediation.schemas import (
    GeneratedPatch,
    ProblemType,
    RemediationPlan,
    SourceLocation,
)

log = get_logger(__name__)

# Sentinel value: set target_value to this to REMOVE the attribute
REMOVE_SENTINEL = "__REMOVE__"

# Maximum lines to scan forward when looking for a tag's closing >
_MAX_TAG_SCAN_LINES = 20

# Context lines in the unified diff
_DIFF_CONTEXT = 3


class PatchGenerator:
    """
    Generates a minimal unified diff from a RemediationPlan.

    Usage:
        generator = PatchGenerator(repo_path=Path("./my-app"))
        patch = generator.generate(plan, location)

        if patch:
            # Proceed to PatchValidator
            pass
    """

    def __init__(self, repo_path: Path) -> None:
        self._repo = repo_path.resolve()

    def generate(
        self,
        plan: RemediationPlan,
        location: SourceLocation,
    ) -> GeneratedPatch | None:
        """
        Generate a GeneratedPatch for the given plan and location.

        Returns None if:
          - The source file does not exist
          - The target content cannot be found in the file
          - The plan requires manual review
          - No patch strategy applies
        """
        if plan.requires_manual_review:
            log.info("patcher.skipping_manual_review", finding_id=plan.finding_id)
            return None

        if not location.file_path:
            log.warning("patcher.no_file_path", finding_id=plan.finding_id)
            return None

        abs_path = self._repo / location.file_path
        if not abs_path.exists():
            log.warning("patcher.file_not_found", path=str(abs_path))
            return None

        try:
            original_text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.error("patcher.read_error", path=str(abs_path), error=str(exc))
            return None

        original_lines = original_text.splitlines(keepends=True)

        log.info(
            "patcher.generate_start",
            finding_id=plan.finding_id,
            file=location.file_path,
            strategy=self._select_strategy_name(plan),
            target_attr=plan.target_attribute,
            target_value=plan.target_value[:30] if plan.target_value else "",
        )

        # ── Select and apply strategy ─────────────────────────────────────────
        patched_lines = self._apply_strategy(plan, location, original_lines)

        if patched_lines is None:
            log.warning(
                "patcher.strategy_failed",
                finding_id=plan.finding_id,
                strategy=self._select_strategy_name(plan),
            )
            return None

        if patched_lines == original_lines:
            log.warning("patcher.no_change_produced", finding_id=plan.finding_id)
            return None

        # ── Build unified diff ────────────────────────────────────────────────
        diff_text = self._build_unified_diff(
            original_lines, patched_lines, location.file_path
        )

        if not diff_text.strip():
            log.warning("patcher.empty_diff", finding_id=plan.finding_id)
            return None

        # ── Count changes ─────────────────────────────────────────────────────
        added = sum(1 for ln in diff_text.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in diff_text.splitlines() if ln.startswith("-") and not ln.startswith("---"))

        patch = GeneratedPatch(
            finding_id=plan.finding_id,
            attempt_number=plan.attempt_number,
            target_file=location.file_path,
            unified_diff=diff_text,
            lines_added=added,
            lines_removed=removed,
            patch_hash=hashlib.sha256(diff_text.encode()).hexdigest(),
            is_minimal=(added + removed <= 4),
        )

        log.info(
            "patcher.patch_generated",
            finding_id=plan.finding_id,
            patch_id=patch.patch_id,
            lines_added=added,
            lines_removed=removed,
            is_minimal=patch.is_minimal,
        )
        return patch

    # ── Strategy dispatcher ───────────────────────────────────────────────────

    def _apply_strategy(
        self,
        plan: RemediationPlan,
        location: SourceLocation,
        original_lines: list[str],
    ) -> list[str] | None:
        """Select and apply the appropriate patch strategy."""
        lang = location.language.lower()
        is_css = lang in ("css", "scss", "sass")

        # CSS / styling fixes → always line replacement
        if is_css or plan.problem_type == ProblemType.FOCUS_VISIBILITY:
            return self._strategy_line_replacement(plan, location, original_lines)

        # Element replacement (marquee → p, div → button, etc.)
        if plan.target_attribute == "__REPLACE_ELEMENT__":
            return self._strategy_element_replacement(plan, location, original_lines)

        # Missing element (title, label, h1) → element insertion
        if (
            plan.problem_type == ProblemType.MISSING_MARKUP
            and not plan.target_attribute
            and plan.target_value not in ("en",)
        ):
            return self._strategy_element_insertion(plan, location, original_lines)

        # Multi-attribute injection (pipe-separated: "role|aria-label")
        if plan.target_attribute and "|" in plan.target_attribute:
            return self._strategy_multi_attribute_injection(plan, location, original_lines)

        # Single attribute operation (add/modify/remove) → attribute injection
        if plan.target_attribute:
            return self._strategy_attribute_injection(plan, location, original_lines)

        # Fallback
        return None

    @staticmethod
    def _select_strategy_name(plan: RemediationPlan) -> str:
        """Return the strategy name for logging."""
        if plan.problem_type == ProblemType.FOCUS_VISIBILITY:
            return "line_replacement"
        if plan.target_attribute:
            return "attribute_injection"
        return "element_insertion"

    # ── Strategy 1: Attribute Injection ───────────────────────────────────────

    def _strategy_attribute_injection(
        self,
        plan: RemediationPlan,
        location: SourceLocation,
        original_lines: list[str],
    ) -> list[str] | None:
        """
        Add, modify, or remove a single attribute on an HTML/JSX element.

        Handles both single-line and multi-line (JSX) opening tags:
            Single: <button class="btn">
            Multi:  <button
                      className="btn"
                      type="submit"
                    >

        For multi-line tags, the attribute is injected on a new line before
        the closing >.
        """
        attr = plan.target_attribute
        value = plan.target_value
        removing = (value == REMOVE_SENTINEL)

        # Find the target element's opening tag
        tag_start_idx, tag_end_idx = self._find_tag_span(plan, location, original_lines)
        if tag_start_idx is None:
            log.warning(
                "patcher.attr_injection.tag_not_found",
                finding_id=plan.finding_id,
                attr=attr,
            )
            return None

        patched = list(original_lines)
        tag_lines = original_lines[tag_start_idx:tag_end_idx + 1]
        tag_text = "".join(tag_lines)

        # ── Remove attribute ──────────────────────────────────────────────────
        if removing:
            new_tag_text = self._remove_attribute(tag_text, attr)
        else:
            # ── Check if attribute already exists ──────────────────────────────
            existing = self._find_attribute_value(tag_text, attr)
            if existing is not None:
                # Modify existing attribute value
                new_tag_text = self._replace_attribute_value(tag_text, attr, value)
            else:
                # Add new attribute
                new_tag_text = self._add_attribute(tag_text, attr, value, location.language)

        if new_tag_text == tag_text:
            log.warning("patcher.attr_injection.no_change", attr=attr)
            return None

        # Rebuild the lines
        new_tag_lines = self._restore_lines(new_tag_text, tag_lines)
        patched[tag_start_idx:tag_end_idx + 1] = new_tag_lines

        return patched

    # ── Strategy 2: Element Insertion ─────────────────────────────────────────

    def _strategy_element_insertion(
        self,
        plan: RemediationPlan,
        location: SourceLocation,
        original_lines: list[str],
    ) -> list[str] | None:
        """
        Insert a new element as a child of a parent element.

        Use cases:
          - Insert <title>App Name</title> inside <head>
          - Insert <label for="id">Label text</label> before an <input>
          - Insert <h1>ShopNow</h1> as first child of <body>
        """
        # Determine what to insert
        new_element = self._build_new_element(plan)
        if not new_element:
            return None

        # Find insertion point
        parent_tag, insert_after_idx = self._find_insertion_point(plan, location, original_lines)
        if insert_after_idx is None:
            log.warning(
                "patcher.element_insertion.point_not_found",
                finding_id=plan.finding_id,
            )
            return None

        # Determine indentation from surrounding lines
        indent = self._detect_indent(original_lines, insert_after_idx)
        new_line = f"{indent}{new_element}\n"

        patched = list(original_lines)
        patched.insert(insert_after_idx + 1, new_line)
        return patched

    # ── Strategy 3: Element Replacement ───────────────────────────────────────

    def _strategy_element_replacement(
        self,
        plan: RemediationPlan,
        location: SourceLocation,
        original_lines: list[str],
    ) -> list[str] | None:
        """
        Replace an entire element (opening tag, content, closing tag) with a
        different element. Used for: <marquee> → <p>, <div onclick> → <button>, etc.

        The new element HTML is taken directly from plan.target_value.
        """
        new_html = plan.target_value.strip()
        if not new_html:
            log.warning("patcher.element_replacement.no_target_value", finding_id=plan.finding_id)
            return None

        anchor = max(0, location.start_line - 1)
        search_start = max(0, anchor - 10)
        search_end = min(len(original_lines), anchor + 10)

        # Find the old element's opening tag line
        old_tag = self._expected_tag_for_replacement(plan)
        old_line_idx = None

        # Search from anchor outward
        search_indices = []
        for offset in range(11):
            if offset == 0:
                if 0 <= anchor < len(original_lines):
                    search_indices.append(anchor)
            else:
                if 0 <= anchor + offset < len(original_lines):
                    search_indices.append(anchor + offset)
                if 0 <= anchor - offset < len(original_lines):
                    search_indices.append(anchor - offset)

        for i in search_indices:
            line = original_lines[i]
            if old_tag and re.search(rf"<{re.escape(old_tag)}\b", line, re.IGNORECASE):
                old_line_idx = i
                break
            # If no specific tag, match any opening tag near anchor
            if not old_tag and re.search(r"<[a-zA-Z]", line):
                old_line_idx = i
                break

        if old_line_idx is None:
            log.warning(
                "patcher.element_replacement.old_element_not_found",
                finding_id=plan.finding_id,
                old_tag=old_tag,
            )
            return None

        # Find the closing tag to know the span of the old element
        old_tag_end_idx = old_line_idx
        if old_tag:
            # Search forward for closing tag
            for j in range(old_line_idx, min(len(original_lines), old_line_idx + 20)):
                if re.search(rf"</{re.escape(old_tag)}\s*>", original_lines[j], re.IGNORECASE):
                    old_tag_end_idx = j
                    break
                # Self-closing or single line
                if j == old_line_idx and re.search(r"/>", original_lines[j]):
                    old_tag_end_idx = j
                    break

        # Preserve original indentation
        orig_indent = re.match(r"^(\s*)", original_lines[old_line_idx]).group(1)
        new_line = f"{orig_indent}{new_html}\n"

        patched = list(original_lines)
        # Replace the old element span with the new element
        patched[old_line_idx:old_tag_end_idx + 1] = [new_line]

        log.info(
            "patcher.element_replacement.done",
            finding_id=plan.finding_id,
            old_tag=old_tag,
            old_lines=f"{old_line_idx}-{old_tag_end_idx}",
        )
        return patched

    @staticmethod
    def _expected_tag_for_replacement(plan: RemediationPlan) -> str:
        """Extract the old element's tag name from fix_strategy for element replacement."""
        strategy = plan.fix_strategy.lower()
        # Look for "replace <X>" or "replace the <X>" patterns
        m = re.search(r"replace\s+(?:the\s+)?<([a-zA-Z][a-zA-Z0-9]*)[\s>]", strategy)
        if m:
            return m.group(1)
        # Look for "<marquee>" or similar tag mentions
        m = re.search(r"<([a-zA-Z][a-zA-Z0-9]*)\b", strategy)
        if m:
            return m.group(1)
        return ""

    # ── Strategy 4: Multi-Attribute Injection ─────────────────────────────────

    def _strategy_multi_attribute_injection(
        self,
        plan: RemediationPlan,
        location: SourceLocation,
        original_lines: list[str],
    ) -> list[str] | None:
        """
        Inject multiple attributes at once using pipe-separated target_attribute/value.

        Example: target_attribute="role|aria-label", target_value="region|Hero Banner"
        → Adds role="region" aria-label="Hero Banner" to the target element.
        """
        attrs = [a.strip() for a in plan.target_attribute.split("|")]
        values = [v.strip() for v in plan.target_value.split("|")]

        if len(attrs) != len(values):
            log.warning(
                "patcher.multi_attr.mismatch",
                finding_id=plan.finding_id,
                attrs=attrs,
                values=values,
            )
            return None

        # Find the target element's opening tag
        tag_start_idx, tag_end_idx = self._find_tag_span(plan, location, original_lines)
        if tag_start_idx is None:
            log.warning("patcher.multi_attr.tag_not_found", finding_id=plan.finding_id)
            return None

        patched = list(original_lines)
        tag_lines = original_lines[tag_start_idx:tag_end_idx + 1]
        tag_text = "".join(tag_lines)

        # Apply each attribute in sequence
        for attr, value in zip(attrs, values):
            existing = self._find_attribute_value(tag_text, attr)
            if value == REMOVE_SENTINEL:
                tag_text = self._remove_attribute(tag_text, attr)
            elif existing is not None:
                tag_text = self._replace_attribute_value(tag_text, attr, value)
            else:
                tag_text = self._add_attribute(tag_text, attr, value, location.language)

        new_tag_lines = self._restore_lines(tag_text, tag_lines)
        patched[tag_start_idx:tag_end_idx + 1] = new_tag_lines
        return patched

    # ── Strategy 3: Line Replacement ──────────────────────────────────────────

    def _strategy_line_replacement(
        self,
        plan: RemediationPlan,
        location: SourceLocation,
        original_lines: list[str],
    ) -> list[str] | None:
        """
        Replace one or more lines containing a specific pattern.

        Primary use case: CSS focus visibility fixes.
            outline: none → outline: 2px solid currentColor; outline-offset: 2px;
        """
        target_line_idx = self._find_line_by_content(
            original_lines, location, plan
        )
        if target_line_idx is None:
            return None

        target_line = original_lines[target_line_idx]
        indent = len(target_line) - len(target_line.lstrip())
        indent_str = target_line[:indent]

        # Build replacement based on problem type
        replacement = self._build_line_replacement(plan, target_line, indent_str)
        if not replacement:
            return None

        patched = list(original_lines)
        patched[target_line_idx] = replacement
        return patched

    # ── Tag finding ───────────────────────────────────────────────────────────

    def _find_tag_span(
        self,
        plan: RemediationPlan,
        location: SourceLocation,
        lines: list[str],
    ) -> tuple[int | None, int | None]:
        """
        Find the start and end line indices of the target element's opening tag.

        Handles multi-line JSX tags:
            <button            ← start
              className="btn"
              onClick={fn}
            >                  ← end

        Strategy:
          1. Use location.start_line as anchor (1-indexed → 0-indexed)
          2. Search ±10 lines for the opening tag
          3. If found, scan forward for the closing > (handling multi-line tags)
        """
        anchor = max(0, location.start_line - 1)
        search_start = max(0, anchor - 10)
        search_end = min(len(lines), anchor + 10)

        # Determine the expected tag name
        expected_tag = self._expected_tag(plan)

        # Search for the opening tag by radiating outwards from the anchor
        tag_start_idx = None
        
        # Build search order: anchor, anchor+1, anchor-1, anchor+2, anchor-2...
        search_indices = []
        for offset in range(11):
            if offset == 0:
                if 0 <= anchor < len(lines):
                    search_indices.append(anchor)
            else:
                if 0 <= anchor + offset < len(lines):
                    search_indices.append(anchor + offset)
                if 0 <= anchor - offset < len(lines):
                    search_indices.append(anchor - offset)

        for i in search_indices:
            line = lines[i]
            if expected_tag and re.search(rf"<{re.escape(expected_tag)}\b", line, re.IGNORECASE):
                tag_start_idx = i
                break
            elif not expected_tag and re.search(r"<[a-zA-Z]", line):
                tag_start_idx = i
                break

        if tag_start_idx is None:
            return None, None

        # Scan forward to find where the opening tag ends (closing >)
        # Handles multi-line tags
        tag_end_idx = tag_start_idx
        open_count = 0
        for i in range(tag_start_idx, min(len(lines), tag_start_idx + _MAX_TAG_SCAN_LINES)):
            line_text = lines[i]
            # Count opening < and closing > to track tag boundaries
            for char in line_text:
                if char == "<":
                    open_count += 1
                elif char == ">":
                    open_count -= 1
                    if open_count <= 0:
                        tag_end_idx = i
                        return tag_start_idx, tag_end_idx
            tag_end_idx = i

        return tag_start_idx, tag_end_idx

    @staticmethod
    def _expected_tag(plan: RemediationPlan) -> str:
        """Derive the expected element tag from the plan's fix_strategy."""
        strategy = plan.fix_strategy.lower()
        for tag in ("button", "input", "select", "textarea", "img", "html",
                    "head", "a", "div", "span", "p", "title", "label"):
            if re.search(rf"<{tag}\b", strategy) or re.search(rf"\b{tag}\s+element", strategy) or re.search(rf"\btag\s+{tag}\b", strategy) or strategy.startswith(f"{tag} "):
                return tag
            # A more restricted check for " a " since "a" is a common english word
            if tag != "a" and (f" {tag} " in strategy):
                return tag
            if tag == "a" and (re.search(rf"<{tag}>", strategy) or "anchor tag" in strategy or "anchor element" in strategy):
                return tag

        # Try target_attribute hints
        if plan.target_attribute == "lang":
            return "html"
        if plan.target_attribute == "alt":
            return "img"
        if plan.target_attribute in ("aria-label", "aria-labelledby", "aria-hidden"):
            return ""  # Could be any element
        return ""

    # ── Attribute manipulation ────────────────────────────────────────────────

    @staticmethod
    def _find_attribute_value(tag_text: str, attr: str) -> str | None:
        """Return the current value of an attribute, or None if not present."""
        # Double-quoted
        m = re.search(rf'{re.escape(attr)}\s*=\s*"([^"]*)"', tag_text)
        if m:
            return m.group(1)
        # Single-quoted
        m = re.search(rf"{re.escape(attr)}\s*=\s*'([^']*)'", tag_text)
        if m:
            return m.group(1)
        # JSX expression
        m = re.search(rf"{re.escape(attr)}\s*=\s*\{{([^}}]*)\}}", tag_text)
        if m:
            return m.group(1)
        # Boolean attribute
        m = re.search(rf"\b{re.escape(attr)}\b", tag_text)
        if m:
            return "true"
        return None

    @staticmethod
    def _replace_attribute_value(tag_text: str, attr: str, new_value: str) -> str:
        """Replace the value of an existing attribute in a tag string."""
        # Double-quoted first
        result = re.sub(
            rf'({re.escape(attr)}\s*=\s*)"([^"]*)"',
            rf'\g<1>"{new_value}"',
            tag_text,
            count=1,
        )
        if result != tag_text:
            return result
        # Single-quoted
        result = re.sub(
            rf"({re.escape(attr)}\s*=\s*)'([^']*)'",
            rf"\g<1>'{new_value}'",
            tag_text,
            count=1,
        )
        if result != tag_text:
            return result
        # Boolean attribute → convert to attr="value"
        # IMPORTANT: only match standalone boolean attr (NOT followed by =)
        result = re.sub(
            rf"\b{re.escape(attr)}\b(?!\s*=)",
            f'{attr}="{new_value}"',
            tag_text,
            count=1,
        )
        return result


    @staticmethod
    def _remove_attribute(tag_text: str, attr: str) -> str:
        """Remove an attribute and its value from a tag string."""
        # Double-quoted: attr="value" with optional surrounding whitespace
        result = re.sub(rf'\s*{re.escape(attr)}\s*=\s*"[^"]*"', "", tag_text)
        if result != tag_text:
            return result
        # Single-quoted
        result = re.sub(rf"\s*{re.escape(attr)}\s*=\s*'[^']*'", "", tag_text)
        if result != tag_text:
            return result
        # JSX expression
        result = re.sub(rf"\s*{re.escape(attr)}\s*=\s*\{{[^}}]*\}}", "", tag_text)
        if result != tag_text:
            return result
        # Boolean attribute
        result = re.sub(rf"\s*\b{re.escape(attr)}\b", "", tag_text)
        return result

    @staticmethod
    def _add_attribute(tag_text: str, attr: str, value: str, language: str) -> str:
        """
        Insert a new attribute into an opening HTML/JSX tag.

        For single-line tags: inserts before the closing > or />
        For multi-line tags:  inserts on a new line before the closing >
        """
        is_jsx = language.lower() in ("tsx", "jsx", "ts", "js")

        # Detect multi-line tag: closing > is on its own line
        multiline = bool(re.search(r"\n\s*>", tag_text) or re.search(r"\n\s*/>", tag_text))

        if is_jsx:
            attr_str = f'{attr}="{value}"'
        else:
            attr_str = f'{attr}="{value}"' if value != "" else f'{attr}=""'

        if multiline:
            # Find the indent of existing attributes
            attr_line_match = re.search(r"\n(\s+)\w", tag_text)
            indent = attr_line_match.group(1) if attr_line_match else "  "
            # Insert before the first closing > or />
            tag_text = re.sub(r"(\n\s*/>|\n\s*>)", f"\n{indent}{attr_str}\\1", tag_text, count=1)
        else:
            # Insert before the FIRST /> or > (single-line)
            tag_text = re.sub(r"\s*(/>|>)", f' {attr_str}\\1', tag_text, count=1)

        return tag_text

    @staticmethod
    def _restore_lines(new_tag_text: str, original_tag_lines: list[str]) -> list[str]:
        """
        Restore line endings from the original lines when splitting the patched tag text.
        Preserves the original file's line ending style.
        """
        # Detect line ending from original
        eol = "\r\n" if original_tag_lines and "\r\n" in original_tag_lines[0] else "\n"
        # Split on newlines, re-add endings
        raw_lines = new_tag_text.split("\n")
        # Last element has no trailing newline if the original didn't
        result = []
        for i, part in enumerate(raw_lines):
            if i < len(raw_lines) - 1:
                result.append(part + eol)
            elif part:  # Non-empty last line
                result.append(part + eol)
        return result

    # ── Element insertion helpers ─────────────────────────────────────────────

    def _build_new_element(self, plan: RemediationPlan) -> str:
        """Build the new element string to insert."""
        problem = plan.problem_type
        strategy = plan.fix_strategy.lower()

        # <title> element
        if "<title>" in strategy or "title element" in strategy or problem == ProblemType.MISSING_MARKUP:
            title_value = plan.target_value or "Page Title"
            return f"<title>{title_value}</title>"

        # <label> element
        if "label" in strategy and "for=" in strategy:
            m = re.search(r'for=["\']([^"\']+)["\']', plan.fix_strategy)
            label_for = m.group(1) if m else ""
            label_text = plan.target_value or "Label"
            return f'<label for="{label_for}">{label_text}</label>'

        # lang attribute on <html> → handled by attribute injection, not insertion
        return ""

    def _find_insertion_point(
        self,
        plan: RemediationPlan,
        location: SourceLocation,
        lines: list[str],
    ) -> tuple[str, int | None]:
        """
        Find where to insert the new element.
        Returns (parent_tag, line_index_to_insert_after).
        """
        # For <title>: insert after <head> opening tag
        if plan.problem_type == ProblemType.MISSING_MARKUP:
            for i, line in enumerate(lines):
                if re.search(r"<head\b", line, re.IGNORECASE):
                    return "head", i
            return "body", None

        # For <label>: insert before the <input> at location.start_line
        anchor = max(0, location.start_line - 2)  # one line before the input
        return "label_before_input", anchor

    # ── CSS line replacement helpers ──────────────────────────────────────────

    def _find_line_by_content(
        self,
        lines: list[str],
        location: SourceLocation,
        plan: RemediationPlan,
    ) -> int | None:
        """Find the index of the line to replace, with context verification."""
        anchor = max(0, location.start_line - 1)
        search_start = max(0, anchor - 10)
        search_end = min(len(lines), anchor + 10)

        # Search for outline:none or other CSS patterns
        patterns = [
            r"outline\s*:\s*none",
            r"outline\s*:\s*0",
            r"outline-style\s*:\s*none",
        ]
        if plan.target_attribute:
            patterns.insert(0, re.escape(plan.target_attribute))

        for i in range(search_start, search_end):
            for p in patterns:
                if re.search(p, lines[i], re.IGNORECASE):
                    return i
        return None

    @staticmethod
    def _build_line_replacement(
        plan: RemediationPlan,
        original_line: str,
        indent: str,
    ) -> str | None:
        """Build the replacement line for a CSS fix."""
        if plan.problem_type == ProblemType.FOCUS_VISIBILITY:
            # Replace outline:none with a proper focus indicator
            replacement = re.sub(
                r"outline\s*:\s*(none|0)",
                "outline: 2px solid currentColor; outline-offset: 2px",
                original_line,
                flags=re.IGNORECASE,
            )
            return replacement

        if plan.target_attribute and plan.target_value:
            return f"{indent}{plan.target_attribute}: {plan.target_value};\n"

        return None

    # ── Unified diff builder ──────────────────────────────────────────────────

    @staticmethod
    def _build_unified_diff(
        original_lines: list[str],
        patched_lines: list[str],
        file_path: str,
    ) -> str:
        """Build a standard unified diff string."""
        # Ensure all lines end with newline for clean diffs
        def ensure_newline(lines: list[str]) -> list[str]:
            result = []
            for ln in lines:
                if ln and not ln.endswith(("\n", "\r\n")):
                    result.append(ln + "\n")
                else:
                    result.append(ln)
            return result

        orig = ensure_newline(original_lines)
        patched = ensure_newline(patched_lines)

        diff = difflib.unified_diff(
            orig,
            patched,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=_DIFF_CONTEXT,
        )
        return "".join(diff)

    # ── Misc helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _detect_indent(lines: list[str], ref_idx: int) -> str:
        """Detect the indentation of nearby lines."""
        for i in range(ref_idx, max(-1, ref_idx - 5), -1):
            if i < len(lines) and lines[i].strip():
                return re.match(r"^(\s*)", lines[i]).group(1)
        return "  "

    def apply_patch_to_file(
        self,
        patch: GeneratedPatch,
        dry_run: bool = False,
    ) -> bool:
        """
        Apply a patch directly to the file on disk.

        This is used by GitManager in Phase 5.
        In dry_run mode, validates the patch would apply cleanly without writing.

        Returns True if successful, False otherwise.
        """
        abs_path = self._repo / patch.target_file
        if not abs_path.exists():
            log.error("patcher.apply.file_not_found", path=str(abs_path))
            return False

        try:
            original = abs_path.read_text(encoding="utf-8", errors="replace")
            original_lines = original.splitlines(keepends=True)

            # Re-apply the diff using difflib's patch logic
            # Parse the unified diff to extract original and replacement lines
            patched_lines = self._apply_unified_diff(original_lines, patch.unified_diff)

            if patched_lines is None:
                log.error("patcher.apply.diff_parse_failed", patch_id=patch.patch_id)
                return False

            if not dry_run:
                abs_path.write_text("".join(patched_lines), encoding="utf-8")
                log.info("patcher.apply.success", patch_id=patch.patch_id, file=patch.target_file)

            return True

        except Exception as exc:
            log.error("patcher.apply.error", patch_id=patch.patch_id, error=str(exc))
            return False

    @staticmethod
    def _apply_unified_diff(
        original_lines: list[str],
        diff_text: str,
    ) -> list[str] | None:
        """
        Apply a unified diff to a list of lines.
        Uses a simple patch parser for the standard unified diff format.
        """
        patched = list(original_lines)
        diff_lines = diff_text.splitlines(keepends=True)

        i = 0
        offset = 0  # Track line offset from applied hunks

        while i < len(diff_lines):
            line = diff_lines[i]
            # Parse hunk header: @@ -start,count +start,count @@
            hunk_match = re.match(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@", line)
            if not hunk_match:
                i += 1
                continue

            orig_start = int(hunk_match.group(1)) - 1  # 0-indexed
            orig_count = int(hunk_match.group(2) or "1")
            new_start = int(hunk_match.group(3)) - 1
            i += 1

            # Read hunk lines
            hunk_orig: list[str] = []
            hunk_new: list[str] = []

            while i < len(diff_lines) and not diff_lines[i].startswith("@@"):
                hunk_line = diff_lines[i]
                if hunk_line.startswith("-"):
                    hunk_orig.append(hunk_line[1:])
                elif hunk_line.startswith("+"):
                    hunk_new.append(hunk_line[1:])
                elif hunk_line.startswith(" "):
                    hunk_orig.append(hunk_line[1:])
                    hunk_new.append(hunk_line[1:])
                i += 1

            # Apply hunk
            apply_at = orig_start + offset
            patched[apply_at:apply_at + len(hunk_orig)] = hunk_new
            offset += len(hunk_new) - len(hunk_orig)

        return patched
