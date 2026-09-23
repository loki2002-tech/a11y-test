"""
Unit tests for Phase 2:
    - SourceAnalyzer  (analyzer.py)
    - RemediationClassifier (classifier.py)

These tests are deterministic (no LLM calls).
All assertions verify structural analysis and rule-table classification logic.

Test groups:
    1. SourceAnalyzer — tag/attribute extraction
    2. SourceAnalyzer — structural signals (icon-only, is_inside_form, etc.)
    3. SourceAnalyzer — problem type classification
    4. RemediationClassifier — SAFE_AUTO_FIX rules
    5. RemediationClassifier — MANUAL_REVIEW_REQUIRED rules
    6. RemediationClassifier — DO_NOT_AUTO_REMEDIATE rules
    7. RemediationClassifier — fallback behaviour
    8. Integration — analyze → classify pipeline
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from accessibility_agent.remediation.analyzer import SourceAnalyzer
from accessibility_agent.remediation.classifier import RemediationClassifier
from accessibility_agent.remediation.schemas import (
    ApplicationFramework,
    ProblemType,
    RemediationAutomationLevel,
    SourceLocation,
    SourceMatchConfidence,
)

# ── Fixture paths ─────────────────────────────────────────────────────────────

FIXTURES = Path(__file__).parent.parent / "fixtures"
HTML_FIXTURE = FIXTURES / "html_missing_label"
REACT_FIXTURE = FIXTURES / "react_missing_aria"
VUE_FIXTURE = FIXTURES / "vue_missing_lang"
DJANGO_FIXTURE = FIXTURES / "django_missing_title"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _location(
    file_path: str,
    start_line: int = 1,
    end_line: int = 5,
    language: str = "html",
    framework: ApplicationFramework = ApplicationFramework.STATIC_HTML,
    confidence: SourceMatchConfidence = SourceMatchConfidence.DIRECT_MATCH,
) -> SourceLocation:
    return SourceLocation(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        language=language,
        framework=framework,
        confidence=confidence,
    )


def _finding(
    rule_id: str = "",
    wcag_sc: str = "",
    description: str = "",
    selector: str = "",
    html: str = "",
    role: str = "",
    text_content: str = "",
    finding_id: str = "A11Y-TEST",
) -> dict[str, Any]:
    return {
        "finding_id": finding_id,
        "rule_id": rule_id,
        "description": description,
        "wcag": {"success_criterion": wcag_sc, "level": "A", "title": ""},
        "element": {
            "selector": selector,
            "html": html,
            "role": role,
            "text_content": text_content,
            "accessible_name": text_content,
        },
    }


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 1 — SourceAnalyzer: tag/attribute extraction
# ═════════════════════════════════════════════════════════════════════════════


class TestSourceAnalyzerExtraction:

    def setup_method(self):
        self.analyzer = SourceAnalyzer(repo_path=HTML_FIXTURE)

    def test_extract_tag_button(self):
        tag = self.analyzer._extract_tag('<button class="register-btn" type="submit">')
        assert tag == "button"

    def test_extract_tag_input(self):
        tag = self.analyzer._extract_tag('<input id="email" type="email" placeholder="Email">')
        assert tag == "input"

    def test_extract_tag_img(self):
        tag = self.analyzer._extract_tag('<img src="/hero.jpg" class="hero-image" />')
        assert tag == "img"

    def test_extract_tag_html(self):
        tag = self.analyzer._extract_tag("<html>")
        assert tag == "html"

    def test_extract_tag_empty_line(self):
        tag = self.analyzer._extract_tag("  const x = 5;")
        assert tag == ""

    def test_extract_attributes_double_quoted(self):
        attrs = self.analyzer._extract_attributes('<button class="btn" type="submit" aria-label="Save">')
        assert attrs.get("class") == "btn"
        assert attrs.get("type") == "submit"
        assert attrs.get("aria-label") == "Save"

    def test_extract_attributes_single_quoted(self):
        attrs = self.analyzer._extract_attributes("<input id='my-input' type='text'>")
        assert attrs.get("id") == "my-input"
        assert attrs.get("type") == "text"

    def test_extract_attributes_boolean(self):
        attrs = self.analyzer._extract_attributes("<input disabled required>")
        assert attrs.get("disabled") == "true"
        assert attrs.get("required") == "true"

    def test_extract_attributes_jsx_expression(self):
        attrs = self.analyzer._extract_attributes('<button onClick={handleClick} className="btn">')
        assert attrs.get("classname") == "btn"
        assert "onclick" in attrs  # JSX expression captured

    def test_extract_attributes_aria_hidden(self):
        attrs = self.analyzer._extract_attributes('<button aria-hidden="true">')
        assert attrs.get("aria-hidden") == "true"


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 2 — SourceAnalyzer: structural signals
# ═════════════════════════════════════════════════════════════════════════════


class TestSourceAnalyzerStructural:

    def setup_method(self):
        self.analyzer = SourceAnalyzer(repo_path=HTML_FIXTURE)

    def test_is_inside_form_when_form_present(self):
        block = '<form action="/submit">\n<input type="email">\n</form>'
        assert self.analyzer._is_inside_form(block) is True

    def test_is_not_inside_form_when_no_form(self):
        block = '<div class="wrapper">\n<button>Click</button>\n</div>'
        assert self.analyzer._is_inside_form(block) is False

    def test_is_icon_only_with_svg(self):
        block = '<button class="close-btn"><svg viewBox="0 0 24 24"></svg></button>'
        assert self.analyzer._is_icon_only(block, "button") is True

    def test_is_icon_only_with_img(self):
        block = '<button class="register-btn"><img src="/check.svg" /></button>'
        assert self.analyzer._is_icon_only(block, "button") is True

    def test_is_not_icon_only_when_has_text(self):
        block = '<button class="register-btn">Register</button>'
        assert self.analyzer._is_icon_only(block, "button") is False

    def test_is_not_icon_only_for_non_interactive(self):
        block = '<div><svg></svg></div>'
        assert self.analyzer._is_icon_only(block, "div") is False

    def test_detect_event_handlers_jsx(self):
        line = '<button onClick={handleRegister} onKeyDown={handleKey}>'
        has, names = self.analyzer._detect_event_handlers(line, line, ApplicationFramework.REACT)
        assert has is True
        assert len(names) >= 1

    def test_detect_event_handlers_html(self):
        line = '<button onclick="toggleMenu()">'
        has, names = self.analyzer._detect_event_handlers(line, line, ApplicationFramework.STATIC_HTML)
        assert has is True

    def test_find_nearby_labels_with_aria_label(self):
        attrs = {"aria-label": "Close dialog"}
        labels = self.analyzer._find_nearby_labels("", attrs)
        assert any("Close dialog" in l for l in labels)

    def test_find_nearby_labels_with_label_element(self):
        block = '<label for="email">Email address</label>\n<input id="email">'
        labels = self.analyzer._find_nearby_labels(block, {})
        assert any("Email address" in l for l in labels)

    def test_infer_component_name_from_tsx(self):
        name = self.analyzer._infer_component_name("src/components/Register.tsx")
        assert name == "Register"

    def test_infer_component_name_strips_suffix(self):
        name = self.analyzer._infer_component_name("src/components/NavBar.component.ts")
        assert "NavBar" in name


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 3 — SourceAnalyzer: problem type from HTML fixtures
# ═════════════════════════════════════════════════════════════════════════════


class TestSourceAnalyzerProblemType:

    def setup_method(self):
        self.analyzer = SourceAnalyzer(repo_path=HTML_FIXTURE)

    def _analyze(self, file_path: str, line: int, finding: dict) -> "SourceContext":
        location = _location(
            file_path=file_path,
            start_line=line,
            end_line=line + 3,
            language="html",
        )
        return self.analyzer.analyze(location, finding)

    def test_button_problem_type_is_incorrect_aria(self):
        ctx = self._analyze(
            "index.html", 29,
            _finding(rule_id="button-name", wcag_sc="4.1.2", description="Button has no accessible name.")
        )
        assert ctx.problem_type == ProblemType.INCORRECT_ARIA

    def test_input_problem_type_is_form_labeling(self):
        ctx = self._analyze(
            "index.html", 20,
            _finding(rule_id="label", wcag_sc="1.3.1", description="Input has no label.")
        )
        assert ctx.problem_type == ProblemType.FORM_LABELING

    def test_image_problem_type_is_image_alt(self):
        ctx = self._analyze(
            "index.html", 44,
            _finding(rule_id="image-alt", wcag_sc="1.1.1", description="Image has no alt.")
        )
        assert ctx.problem_type == ProblemType.IMAGE_ALT

    def test_html_lang_problem_type_is_missing_markup(self):
        # The html fixture has no lang attr on <html>
        ctx = self._analyze(
            "index.html", 1,
            _finding(rule_id="html-has-lang", wcag_sc="3.1.1", description="html has no lang.")
        )
        assert ctx.problem_type == ProblemType.MISSING_MARKUP

    def test_analyze_react_register_button(self):
        analyzer = SourceAnalyzer(repo_path=REACT_FIXTURE)
        loc = _location(
            file_path="src/components/Register.tsx",
            start_line=34,
            end_line=40,
            language="tsx",
            framework=ApplicationFramework.REACT,
        )
        finding = _finding(rule_id="button-name", wcag_sc="4.1.2")
        ctx = analyzer.analyze(loc, finding)

        assert ctx.element_tag == "button"
        assert ctx.component_name == "Register"
        assert ctx.has_event_handlers is True


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 4 — RemediationClassifier: SAFE_AUTO_FIX rules
# ═════════════════════════════════════════════════════════════════════════════


class TestClassifierSafeAutoFix:

    def setup_method(self):
        self.clf = RemediationClassifier()
        self.analyzer = SourceAnalyzer(repo_path=HTML_FIXTURE)

    def _classify(self, rule_id, wcag_sc, tag, html="", is_icon_only=False, is_inside_form=False):
        from accessibility_agent.remediation.schemas import SourceContext
        ctx = SourceContext(
            file_path="index.html", start_line=1, end_line=5,
            language="html", framework=ApplicationFramework.STATIC_HTML,
            element_tag=tag,
            is_icon_only=is_icon_only,
            is_inside_form=is_inside_form,
            problem_type=ProblemType.UNKNOWN,
        )
        finding = _finding(rule_id=rule_id, wcag_sc=wcag_sc, html=html)
        return self.clf.classify(finding, ctx)

    def test_html_lang_is_safe_auto_fix(self):
        level, _, confidence, _ = self._classify("html-has-lang", "3.1.1", "html")
        assert level == RemediationAutomationLevel.SAFE_AUTO_FIX
        assert confidence >= 0.95

    def test_html_lang_valid_is_safe_auto_fix(self):
        level, _, _, _ = self._classify("html-lang-valid", "3.1.1", "html")
        assert level == RemediationAutomationLevel.SAFE_AUTO_FIX

    def test_icon_only_button_is_safe_auto_fix(self):
        level, ptype, confidence, _ = self._classify(
            "button-name", "4.1.2", "button", is_icon_only=True
        )
        assert level == RemediationAutomationLevel.SAFE_AUTO_FIX
        assert ptype == ProblemType.INCORRECT_ARIA
        assert confidence >= 0.90

    def test_positive_tabindex_is_safe_auto_fix(self):
        level, ptype, _, _ = self._classify("tabindex", "2.4.3", "a")
        assert level == RemediationAutomationLevel.SAFE_AUTO_FIX
        assert ptype == ProblemType.KEYBOARD_ACCESS

    def test_aria_hidden_on_button_is_safe_auto_fix(self):
        level, ptype, _, _ = self._classify("aria-hidden-focus", "4.1.2", "button")
        assert level == RemediationAutomationLevel.SAFE_AUTO_FIX
        assert ptype == ProblemType.INCORRECT_ARIA

    def test_aria_hidden_on_link_is_safe_auto_fix(self):
        level, _, _, _ = self._classify("aria-hidden-focus", "4.1.2", "a")
        assert level == RemediationAutomationLevel.SAFE_AUTO_FIX


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 5 — RemediationClassifier: MANUAL_REVIEW_REQUIRED rules
# ═════════════════════════════════════════════════════════════════════════════


class TestClassifierManualReview:

    def setup_method(self):
        self.clf = RemediationClassifier()

    def _classify(self, rule_id, wcag_sc, tag, text_content="", is_icon_only=False):
        from accessibility_agent.remediation.schemas import SourceContext
        ctx = SourceContext(
            file_path="index.html", start_line=1, end_line=5,
            language="html", framework=ApplicationFramework.STATIC_HTML,
            element_tag=tag,
            is_icon_only=is_icon_only,
            problem_type=ProblemType.UNKNOWN,
        )
        finding = _finding(rule_id=rule_id, wcag_sc=wcag_sc, text_content=text_content)
        return self.clf.classify(finding, ctx)

    def test_link_with_generic_text_is_manual_review(self):
        level, ptype, _, _ = self._classify("link-name", "2.4.4", "a", text_content="click here")
        assert level == RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED
        assert ptype == ProblemType.LINK_TEXT

    def test_informational_image_alt_is_manual_review(self):
        level, ptype, _, _ = self._classify("image-alt", "1.1.1", "img", is_icon_only=False)
        assert level == RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED
        assert ptype == ProblemType.IMAGE_ALT

    def test_heading_order_is_manual_review(self):
        level, ptype, _, _ = self._classify("heading-order", "1.3.1", "h3")
        assert level == RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED
        assert ptype == ProblemType.SEMANTIC_STRUCTURE


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 6 — RemediationClassifier: DO_NOT_AUTO_REMEDIATE
# ═════════════════════════════════════════════════════════════════════════════


class TestClassifierDoNotRemediate:

    def setup_method(self):
        self.clf = RemediationClassifier()

    def _classify(self, rule_id, wcag_sc, tag="span"):
        from accessibility_agent.remediation.schemas import SourceContext
        ctx = SourceContext(
            file_path="index.html", start_line=1, end_line=5,
            language="html", framework=ApplicationFramework.STATIC_HTML,
            element_tag=tag,
            problem_type=ProblemType.UNKNOWN,
        )
        finding = _finding(rule_id=rule_id, wcag_sc=wcag_sc)
        return self.clf.classify(finding, ctx)

    def test_color_contrast_is_do_not_auto_remediate(self):
        level, ptype, confidence, _ = self._classify("color-contrast", "1.4.3")
        assert level == RemediationAutomationLevel.DO_NOT_AUTO_REMEDIATE
        assert ptype == ProblemType.COLOR_CONTRAST
        assert confidence >= 0.99

    def test_color_contrast_enhanced_is_do_not_auto_remediate(self):
        level, _, _, _ = self._classify("color-contrast-enhanced", "1.4.11")
        assert level == RemediationAutomationLevel.DO_NOT_AUTO_REMEDIATE


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 7 — RemediationClassifier: fallback behaviour
# ═════════════════════════════════════════════════════════════════════════════


class TestClassifierFallback:

    def setup_method(self):
        self.clf = RemediationClassifier()

    def test_unknown_rule_returns_classification(self):
        """Any finding — even unknown — must receive a classification."""
        from accessibility_agent.remediation.schemas import SourceContext
        ctx = SourceContext(
            file_path="index.html", start_line=1, end_line=5,
            language="html", framework=ApplicationFramework.UNKNOWN,
            element_tag="div",
            problem_type=ProblemType.UNKNOWN,
        )
        finding = _finding(rule_id="some-unknown-rule-xyz", wcag_sc="9.9.9")
        level, ptype, confidence, reasoning = self.clf.classify(finding, ctx)

        assert level is not None
        assert ptype is not None
        assert 0.0 <= confidence <= 1.0
        assert len(reasoning) > 0

    def test_color_contrast_problem_type_returns_do_not_remediate(self):
        """Even without matching rule_id, color contrast should be safe."""
        from accessibility_agent.remediation.schemas import SourceContext
        ctx = SourceContext(
            file_path="index.html", start_line=1, end_line=5,
            language="css", framework=ApplicationFramework.STATIC_HTML,
            element_tag="span",
            problem_type=ProblemType.COLOR_CONTRAST,  # Set by analyzer
        )
        finding = _finding(rule_id="xyz", wcag_sc="1.4.3")
        level, _, _, _ = self.clf.classify(finding, ctx)
        # The rule table should match on wcag_sc="1.4.3"
        assert level == RemediationAutomationLevel.DO_NOT_AUTO_REMEDIATE


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 8 — Integration: Analyze → Classify pipeline
# ═════════════════════════════════════════════════════════════════════════════


class TestAnalyzeClassifyPipeline:
    """End-to-end test: SourceAnalyzer output feeds directly into RemediationClassifier."""

    def test_html_button_pipeline(self):
        """HTML fixture: button.register-btn → analyze → classify → SAFE_AUTO_FIX."""
        analyzer = SourceAnalyzer(repo_path=HTML_FIXTURE)
        classifier = RemediationClassifier()

        # Locate the button in the fixture
        loc = _location(
            file_path="index.html",
            start_line=29,
            end_line=32,
            language="html",
            framework=ApplicationFramework.STATIC_HTML,
        )
        finding = _finding(
            rule_id="button-name",
            wcag_sc="4.1.2",
            description="Button has no accessible name.",
            selector=".register-btn",
            html='<button class="register-btn" type="submit">',
        )

        ctx = analyzer.analyze(loc, finding)
        level, ptype, confidence, reasoning = classifier.classify(finding, ctx)

        assert ctx.element_tag == "button"
        assert ptype == ProblemType.INCORRECT_ARIA
        # icon-only check: the fixture button has <img> child → icon-only
        if ctx.is_icon_only:
            assert level == RemediationAutomationLevel.SAFE_AUTO_FIX
        else:
            assert level in (
                RemediationAutomationLevel.SAFE_AUTO_FIX,
                RemediationAutomationLevel.LIKELY_AUTO_FIX,
            )
        assert confidence > 0.5

    def test_react_button_pipeline(self):
        """React fixture: .register-btn → analyze → classify → SAFE or LIKELY."""
        analyzer = SourceAnalyzer(repo_path=REACT_FIXTURE)
        classifier = RemediationClassifier()

        loc = _location(
            file_path="src/components/Register.tsx",
            start_line=34,
            end_line=42,
            language="tsx",
            framework=ApplicationFramework.REACT,
        )
        finding = _finding(
            rule_id="button-name",
            wcag_sc="4.1.2",
            description="Button has no accessible name.",
        )

        ctx = analyzer.analyze(loc, finding)
        level, ptype, confidence, _ = classifier.classify(finding, ctx)

        assert ctx.element_tag == "button"
        assert ptype == ProblemType.INCORRECT_ARIA
        assert level in (
            RemediationAutomationLevel.SAFE_AUTO_FIX,
            RemediationAutomationLevel.LIKELY_AUTO_FIX,
        )

    def test_color_contrast_pipeline_blocks(self):
        """Color contrast issues must ALWAYS be blocked from auto-remediation."""
        from accessibility_agent.remediation.schemas import SourceContext
        analyzer = SourceAnalyzer(repo_path=HTML_FIXTURE)
        classifier = RemediationClassifier()

        ctx = SourceContext(
            file_path="index.html",
            start_line=1, end_line=5,
            language="css",
            framework=ApplicationFramework.STATIC_HTML,
            element_tag="p",
            problem_type=ProblemType.COLOR_CONTRAST,
        )
        finding = _finding(rule_id="color-contrast", wcag_sc="1.4.3")
        level, _, _, _ = classifier.classify(finding, ctx)

        assert level == RemediationAutomationLevel.DO_NOT_AUTO_REMEDIATE, (
            "Color contrast must NEVER be auto-remediated — safety invariant violated!"
        )
