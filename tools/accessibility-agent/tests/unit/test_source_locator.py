"""
Unit tests for the SourceLocator and FrameworkDetector.

Tests are organized in 5 groups:
    1. FrameworkDetector — correctly identifies all supported frameworks
    2. SourceLocator (HTML) — locates bugs in static HTML fixtures
    3. SourceLocator (React) — locates bugs in TSX component fixtures
    4. SourceLocator (Vue) — locates bugs in Vue SFC fixtures
    5. SourceLocator (Django) — locates bugs in Django template fixtures
    6. Confidence model — verifies DIRECT / LIKELY / AMBIGUOUS / NOT_FOUND logic
    7. Edge cases — empty selectors, missing files, excluded directories
"""

from __future__ import annotations

from pathlib import Path

import pytest

from accessibility_agent.remediation.locator import FrameworkDetector, SourceLocator
from accessibility_agent.remediation.schemas import (
    ApplicationFramework,
    SourceMatchConfidence,
)

# ── Fixture paths ─────────────────────────────────────────────────────────────

FIXTURES = Path(__file__).parent.parent / "fixtures"
HTML_FIXTURE = FIXTURES / "html_missing_label"
REACT_FIXTURE = FIXTURES / "react_missing_aria"
VUE_FIXTURE = FIXTURES / "vue_missing_lang"
DJANGO_FIXTURE = FIXTURES / "django_missing_title"


# ── Helper to build a minimal finding dict ────────────────────────────────────

def _finding(
    selector: str = "",
    html: str = "",
    role: str = "",
    accessible_name: str = "",
    text_content: str = "",
    description: str = "",
    finding_id: str = "A11Y-TEST",
) -> dict:
    return {
        "finding_id": finding_id,
        "description": description,
        "element": {
            "selector": selector,
            "html": html,
            "role": role,
            "accessible_name": accessible_name,
            "text_content": text_content,
        },
    }


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 1 — Framework Detector
# ═════════════════════════════════════════════════════════════════════════════


class TestFrameworkDetector:
    """FrameworkDetector correctly identifies frameworks from filesystem markers."""

    def setup_method(self):
        self.detector = FrameworkDetector()

    def test_detects_static_html(self):
        fw = self.detector.detect(HTML_FIXTURE)
        assert fw == ApplicationFramework.STATIC_HTML

    def test_detects_react(self):
        fw = self.detector.detect(REACT_FIXTURE)
        assert fw == ApplicationFramework.REACT

    def test_detects_vue(self):
        fw = self.detector.detect(VUE_FIXTURE)
        assert fw == ApplicationFramework.VUE

    def test_detects_django(self):
        fw = self.detector.detect(DJANGO_FIXTURE)
        assert fw == ApplicationFramework.DJANGO

    def test_returns_unknown_for_empty_dir(self, tmp_path):
        fw = self.detector.detect(tmp_path)
        assert fw == ApplicationFramework.UNKNOWN

    def test_returns_unknown_for_nonexistent_dir(self, tmp_path):
        fw = self.detector.detect(tmp_path / "does_not_exist")
        assert fw == ApplicationFramework.UNKNOWN


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 2 — HTML Fixture: SourceLocator
# ═════════════════════════════════════════════════════════════════════════════


class TestSourceLocatorHTML:
    """SourceLocator correctly finds accessibility bugs in static HTML files."""

    def setup_method(self):
        self.locator = SourceLocator(repo_path=HTML_FIXTURE)

    def test_framework_is_detected_as_static_html(self):
        assert self.locator._framework == ApplicationFramework.STATIC_HTML

    def test_locate_button_by_class_name(self):
        """Finding: button.register-btn has no accessible name."""
        finding = _finding(
            selector=".register-btn",
            html='<button class="register-btn" type="submit">',
            role="button",
            description="Button does not have an accessible name.",
            finding_id="A11Y-HTML-001",
        )
        result = self.locator.locate(finding)

        assert result.confidence != SourceMatchConfidence.NOT_FOUND, (
            f"Expected to find .register-btn — got NOT_FOUND.\n"
            f"Strategy: {result.search_strategy}"
        )
        assert "index.html" in result.file_path, (
            f"Expected index.html, got: {result.file_path}"
        )
        assert result.start_line > 0

    def test_locate_input_by_id(self):
        """Finding: input#full-name has no associated label."""
        finding = _finding(
            selector="#full-name",
            html='<input id="full-name" class="form-input" type="text">',
            role="textbox",
            description="Form input has no associated label element.",
            finding_id="A11Y-HTML-002",
        )
        result = self.locator.locate(finding)

        assert result.confidence != SourceMatchConfidence.NOT_FOUND
        assert "index.html" in result.file_path

    def test_locate_image_by_class(self):
        """Finding: img.hero-image has no alt attribute."""
        finding = _finding(
            selector=".hero-image",
            html='<img src="/hero.jpg" class="hero-image" />',
            role="img",
            description="Image has no alt text.",
            finding_id="A11Y-HTML-003",
        )
        result = self.locator.locate(finding)

        assert result.confidence != SourceMatchConfidence.NOT_FOUND
        assert "index.html" in result.file_path

    def test_locate_nav_link_by_class(self):
        """Finding: a.nav-link has generic link text."""
        finding = _finding(
            selector=".nav-link",
            html='<a href="/about" class="nav-link">Click here</a>',
            role="link",
            accessible_name="Click here",
            text_content="Click here",
            description="Link has generic text 'Click here'.",
            finding_id="A11Y-HTML-004",
        )
        result = self.locator.locate(finding)

        assert result.confidence != SourceMatchConfidence.NOT_FOUND

    def test_locate_menu_toggle_button(self):
        """Finding: button.menu-toggle is empty, no accessible name."""
        finding = _finding(
            selector=".menu-toggle",
            html='<button class="menu-toggle" onclick="toggleMenu()"></button>',
            role="button",
            description="Button has no accessible name.",
            finding_id="A11Y-HTML-005",
        )
        result = self.locator.locate(finding)

        assert result.confidence != SourceMatchConfidence.NOT_FOUND
        assert "index.html" in result.file_path

    def test_not_found_for_nonexistent_selector(self):
        """Finding with a class that doesn't exist in the fixture."""
        finding = _finding(
            selector=".this-class-does-not-exist-anywhere-in-the-fixture",
            html="<div></div>",
            description="Some issue.",
            finding_id="A11Y-HTML-000",
        )
        result = self.locator.locate(finding)

        assert result.confidence == SourceMatchConfidence.NOT_FOUND
        assert not result.is_actionable


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 3 — React Fixture: SourceLocator
# ═════════════════════════════════════════════════════════════════════════════


class TestSourceLocatorReact:
    """SourceLocator correctly finds accessibility bugs in React TSX components."""

    def setup_method(self):
        self.locator = SourceLocator(repo_path=REACT_FIXTURE)

    def test_framework_is_detected_as_react(self):
        assert self.locator._framework == ApplicationFramework.REACT

    def test_locate_register_button_by_classname(self):
        """
        Finding: button.register-btn has no accessible name.
        In React, class → className. Should find Register.tsx.
        """
        finding = _finding(
            selector=".register-btn",
            html='<button class="register-btn" type="submit">',
            role="button",
            description="Button does not have an accessible name.",
            finding_id="A11Y-REACT-001",
        )
        result = self.locator.locate(finding)

        assert result.confidence != SourceMatchConfidence.NOT_FOUND, (
            f"Expected to find .register-btn in React fixture — got NOT_FOUND.\n"
            f"Strategy: {result.search_strategy}, file: {result.file_path}"
        )
        assert "Register" in result.file_path or "register" in result.file_path.lower(), (
            f"Expected Register.tsx, got: {result.file_path}"
        )

    def test_locate_email_input_by_id(self):
        """Finding: input#email-input has no associated label."""
        finding = _finding(
            selector="#email-input",
            html='<input id="email-input" className="form-input form-input--email" type="email">',
            role="textbox",
            description="Input has no label.",
            finding_id="A11Y-REACT-002",
        )
        result = self.locator.locate(finding)

        assert result.confidence != SourceMatchConfidence.NOT_FOUND

    def test_locate_form_input_by_class(self):
        """Finding: input.form-input--email has no label."""
        finding = _finding(
            selector=".form-input--email",
            html='<input className="form-input form-input--email" type="email">',
            role="textbox",
            description="Input missing label.",
            finding_id="A11Y-REACT-003",
        )
        result = self.locator.locate(finding)

        assert result.confidence != SourceMatchConfidence.NOT_FOUND

    def test_matched_text_is_populated(self):
        """The matched_text field should contain the actual source line."""
        finding = _finding(
            selector=".register-btn",
            html='<button class="register-btn">',
            description="Button no name.",
            finding_id="A11Y-REACT-004",
        )
        result = self.locator.locate(finding)

        if result.confidence != SourceMatchConfidence.NOT_FOUND:
            assert result.matched_text.strip() != "", (
                "matched_text should be populated when a match is found"
            )

    def test_context_before_and_after_populated(self):
        """context_before and context_after should be non-empty."""
        finding = _finding(
            selector=".register-btn",
            description="Button no name.",
            finding_id="A11Y-REACT-005",
        )
        result = self.locator.locate(finding)

        if result.confidence != SourceMatchConfidence.NOT_FOUND:
            # At least one of before/after should be non-empty
            assert result.context_before or result.context_after, (
                "Context should be captured around the match"
            )


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 4 — Vue Fixture: SourceLocator
# ═════════════════════════════════════════════════════════════════════════════


class TestSourceLocatorVue:
    """SourceLocator correctly finds accessibility bugs in Vue SFC files."""

    def setup_method(self):
        self.locator = SourceLocator(repo_path=VUE_FIXTURE)

    def test_framework_is_detected_as_vue(self):
        assert self.locator._framework == ApplicationFramework.VUE

    def test_locate_hero_cta_button(self):
        """Finding: button.hero-cta has no accessible name."""
        finding = _finding(
            selector=".hero-cta",
            html='<button class="hero-cta">',
            role="button",
            description="Button has no accessible name.",
            finding_id="A11Y-VUE-001",
        )
        result = self.locator.locate(finding)

        assert result.confidence != SourceMatchConfidence.NOT_FOUND
        assert ".vue" in result.file_path or "vue" in result.file_path.lower(), (
            f"Expected .vue file, got: {result.file_path}"
        )

    def test_locate_hero_image(self):
        """Finding: img.hero-image has no alt attribute."""
        finding = _finding(
            selector=".hero-image",
            html='<img src="/images/hero.jpg" class="hero-image" />',
            role="img",
            description="Image missing alt.",
            finding_id="A11Y-VUE-002",
        )
        result = self.locator.locate(finding)

        assert result.confidence != SourceMatchConfidence.NOT_FOUND


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 5 — Django Fixture: SourceLocator
# ═════════════════════════════════════════════════════════════════════════════


class TestSourceLocatorDjango:
    """SourceLocator correctly finds accessibility bugs in Django template files."""

    def setup_method(self):
        self.locator = SourceLocator(repo_path=DJANGO_FIXTURE)

    def test_framework_is_detected_as_django(self):
        assert self.locator._framework == ApplicationFramework.DJANGO

    def test_locate_back_to_top_button(self):
        """Finding: button.back-to-top has no accessible name."""
        finding = _finding(
            selector=".back-to-top",
            html='<button class="back-to-top" onclick="scrollToTop()">',
            role="button",
            description="Button has no accessible name.",
            finding_id="A11Y-DJANGO-001",
        )
        result = self.locator.locate(finding)

        assert result.confidence != SourceMatchConfidence.NOT_FOUND
        assert ".html" in result.file_path

    def test_locate_logo_image(self):
        """Finding: img.logo-img has no alt attribute."""
        finding = _finding(
            selector=".logo-img",
            html='<img src="/logo.png" class="logo-img" />',
            role="img",
            description="Image missing alt text.",
            finding_id="A11Y-DJANGO-002",
        )
        result = self.locator.locate(finding)

        assert result.confidence != SourceMatchConfidence.NOT_FOUND


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 6 — Confidence Model
# ═════════════════════════════════════════════════════════════════════════════


class TestConfidenceModel:
    """SourceLocation confidence levels are correctly computed."""

    def test_direct_match_is_actionable(self):
        from accessibility_agent.remediation.schemas import SourceLocation

        loc = SourceLocation(
            file_path="src/components/Register.tsx",
            start_line=36,
            end_line=38,
            language="tsx",
            confidence=SourceMatchConfidence.DIRECT_MATCH,
        )
        assert loc.is_actionable is True

    def test_likely_match_is_actionable(self):
        from accessibility_agent.remediation.schemas import SourceLocation

        loc = SourceLocation(
            file_path="src/components/Register.tsx",
            start_line=36,
            end_line=38,
            language="tsx",
            confidence=SourceMatchConfidence.LIKELY_MATCH,
        )
        assert loc.is_actionable is True

    def test_ambiguous_match_is_not_actionable(self):
        from accessibility_agent.remediation.schemas import SourceLocation

        loc = SourceLocation(
            file_path="src/components/Register.tsx",
            start_line=36,
            end_line=38,
            language="tsx",
            confidence=SourceMatchConfidence.AMBIGUOUS_MATCH,
        )
        assert loc.is_actionable is False

    def test_not_found_is_not_actionable(self):
        from accessibility_agent.remediation.schemas import SourceLocation

        loc = SourceLocation(
            file_path="",
            start_line=1,
            end_line=1,
            language="unknown",
            confidence=SourceMatchConfidence.NOT_FOUND,
        )
        assert loc.is_actionable is False

    def test_line_count_property(self):
        from accessibility_agent.remediation.schemas import SourceLocation

        loc = SourceLocation(
            file_path="src/test.tsx",
            start_line=10,
            end_line=15,
            language="tsx",
            confidence=SourceMatchConfidence.DIRECT_MATCH,
        )
        assert loc.line_count == 6  # 15 - 10 + 1


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 7 — Edge Cases
# ═════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """SourceLocator handles edge cases gracefully without exceptions."""

    def test_empty_selector_returns_not_found(self):
        locator = SourceLocator(repo_path=HTML_FIXTURE)
        result = locator.locate(_finding(selector="", html="", description=""))
        assert result.confidence == SourceMatchConfidence.NOT_FOUND

    def test_empty_html_does_not_crash(self):
        locator = SourceLocator(repo_path=HTML_FIXTURE)
        result = locator.locate(_finding(
            selector=".some-class",
            html="",
            description="issue"
        ))
        # Should not raise — just return whatever confidence is appropriate

    def test_very_long_selector_does_not_crash(self):
        locator = SourceLocator(repo_path=HTML_FIXTURE)
        long_selector = ".very-long-class-name-that-is-definitely-not-in-any-fixture-file"
        result = locator.locate(_finding(selector=long_selector, description="issue"))
        assert result.confidence == SourceMatchConfidence.NOT_FOUND

    def test_generic_class_names_handled(self):
        """Generic class names like .btn should not crash the locator."""
        locator = SourceLocator(repo_path=HTML_FIXTURE)
        result = locator.locate(_finding(selector=".btn", description="issue"))
        # May or may not find something — just must not raise

    def test_framework_detector_handles_missing_package_json(self, tmp_path):
        """A directory with only HTML files and no package.json."""
        html_dir = tmp_path / "mysite"
        html_dir.mkdir()
        (html_dir / "index.html").write_text("<html><body>Hello</body></html>")

        detector = FrameworkDetector()
        fw = detector.detect(html_dir)
        assert fw == ApplicationFramework.STATIC_HTML

    def test_source_locator_invalid_repo_raises(self, tmp_path):
        """SourceLocator raises ValueError for non-existent repo path."""
        with pytest.raises(ValueError, match="does not exist"):
            SourceLocator(repo_path=tmp_path / "nonexistent_repo_12345")

    def test_source_location_end_line_must_gte_start_line(self):
        """SourceLocation validates that end_line >= start_line."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            from accessibility_agent.remediation.schemas import SourceLocation
            SourceLocation(
                file_path="src/test.tsx",
                start_line=20,
                end_line=5,   # end < start → should fail
                language="tsx",
                confidence=SourceMatchConfidence.DIRECT_MATCH,
            )
