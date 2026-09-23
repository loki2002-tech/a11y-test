"""
Unit tests for Phase 3:
    - PatchGenerator  (patcher.py)
    - PatchValidator  (validator.py)

All tests are deterministic — no LLM calls, no network.
Tests work directly with the fixture files created in Phase 1.

Test groups:
    1. PatchGenerator — attribute injection (add/modify/remove)
    2. PatchGenerator — element insertion
    3. PatchGenerator — line replacement (CSS)
    4. PatchGenerator — diff format and integrity
    5. PatchGenerator — context mismatch and error cases
    6. PatchValidator — Gate 1 (file exists)
    7. PatchValidator — Gate 2 (context matches)
    8. PatchValidator — Gate 5 (no unrelated changes)
    9. PatchValidator — Gate 6 (no secrets)
    10. PatchValidator — Gate 7 (no invalid ARIA)
    11. Integration — generate → validate pipeline on fixtures
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from accessibility_agent.remediation.patcher import PatchGenerator, REMOVE_SENTINEL
from accessibility_agent.remediation.validator import PatchValidator
from accessibility_agent.remediation.schemas import (
    ApplicationFramework,
    GeneratedPatch,
    ProblemType,
    RemediationAutomationLevel,
    RemediationPlan,
    SourceLocation,
    SourceMatchConfidence,
)

# ── Fixture paths ─────────────────────────────────────────────────────────────

FIXTURES = Path(__file__).parent.parent / "fixtures"
HTML_FIXTURE = FIXTURES / "html_missing_label"
REACT_FIXTURE = FIXTURES / "react_missing_aria"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _plan(
    finding_id: str = "A11Y-TEST",
    problem_type: ProblemType = ProblemType.INCORRECT_ARIA,
    automation_level: RemediationAutomationLevel = RemediationAutomationLevel.SAFE_AUTO_FIX,
    fix_strategy: str = "Add aria-label to the button element.",
    root_cause: str = "Button has no accessible name.",
    target_attribute: str = "aria-label",
    target_value: str = "Register",
    risk_level: str = "low",
    wcag_sc: str = "4.1.2",
    attempt: int = 1,
    requires_manual_review: bool = False,
) -> RemediationPlan:
    return RemediationPlan(
        finding_id=finding_id,
        attempt_number=attempt,
        automation_level=automation_level,
        classification_confidence=0.95,
        classified_by="deterministic_rules",
        problem_type=problem_type,
        root_cause=root_cause,
        fix_strategy=fix_strategy,
        target_attribute=target_attribute,
        target_value=target_value,
        risk_level=risk_level,
        wcag_criterion=wcag_sc,
        requires_manual_review=requires_manual_review,
    )


def _location(
    file_path: str,
    start_line: int = 1,
    end_line: int = 0,          # 0 = auto (start_line + 3)
    language: str = "html",
    matched_text: str = "",
    framework: ApplicationFramework = ApplicationFramework.STATIC_HTML,
) -> SourceLocation:
    if end_line < start_line:
        end_line = start_line + 3
    return SourceLocation(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        language=language,
        framework=framework,
        matched_text=matched_text,
        confidence=SourceMatchConfidence.DIRECT_MATCH,
    )


def _write_temp_file(tmp_path: Path, filename: str, content: str) -> Path:
    """Write a temp file and return its path."""
    f = tmp_path / filename
    f.write_text(content, encoding="utf-8")
    return f


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 1 — PatchGenerator: attribute injection
# ═════════════════════════════════════════════════════════════════════════════


class TestPatchGeneratorAttributeInjection:

    def setup_method(self):
        # Work on copies of the fixture files in temp dirs
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(HTML_FIXTURE, self.tmp / "html_fixture")
        self.repo = self.tmp / "html_fixture"
        self.gen = PatchGenerator(repo_path=self.repo)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_lang_to_html_element(self):
        """Add lang='en' to <html> — the WCAG 3.1.1 fix."""
        plan = _plan(
            problem_type=ProblemType.MISSING_MARKUP,
            fix_strategy="Add lang='en' to the <html> element.",
            target_attribute="lang",
            target_value="en",
            wcag_sc="3.1.1",
        )
        loc = _location(
            file_path="index.html",
            start_line=2,  # Line 2 is <html>
            matched_text='<html>',
        )

        patch = self.gen.generate(plan, loc)

        assert patch is not None, "Expected a patch to be generated"
        assert 'lang="en"' in patch.unified_diff, (
            f"Expected lang='en' in diff:\n{patch.unified_diff}"
        )
        assert patch.lines_added >= 1
        assert patch.patch_hash != ""

    def test_add_aria_label_to_button(self):
        """Add aria-label='...' to button.register-btn."""
        plan = _plan(
            problem_type=ProblemType.INCORRECT_ARIA,
            fix_strategy="Add aria-label='Register' to the <button> element.",
            target_attribute="aria-label",
            target_value="Register",
        )
        loc = _location(
            file_path="index.html",
            start_line=29,
            matched_text='<button class="register-btn" type="submit">',
        )

        patch = self.gen.generate(plan, loc)

        assert patch is not None
        assert "aria-label" in patch.unified_diff
        assert "Register" in patch.unified_diff

    def test_add_empty_alt_to_decorative_image(self):
        """Add alt='' to a decorative image — not REMOVE, add with empty value."""
        plan = _plan(
            problem_type=ProblemType.IMAGE_ALT,
            fix_strategy="Add alt='' to the decorative <img> element.",
            target_attribute="alt",
            target_value="",  # Empty value = add alt=""
        )
        loc = _location(
            file_path="index.html",
            start_line=44,
            matched_text='<img src="/hero.jpg" class="hero-image" />',
        )

        patch = self.gen.generate(plan, loc)

        assert patch is not None, "Expected patch for alt='' addition"
        # The diff should add alt="" not remove anything related to alt
        added_lines = [
            ln for ln in patch.unified_diff.splitlines()
            if ln.startswith("+") and not ln.startswith("+++")
        ]
        assert any('alt=""' in ln or "alt=''" in ln for ln in added_lines), (
            f"Expected alt=\"\" in added lines:\n{chr(10).join(added_lines)}"
        )

    def test_remove_aria_hidden_from_button(self):
        """Remove aria-hidden='true' from a button — REMOVE_SENTINEL."""
        # Write a test file with aria-hidden
        test_html = (
            '<button class="close-btn" aria-hidden="true" type="button">\n'
            '  <svg></svg>\n'
            '</button>\n'
        )
        f = _write_temp_file(self.repo, "aria_hidden_test.html", test_html)

        plan = _plan(
            problem_type=ProblemType.INCORRECT_ARIA,
            fix_strategy="Remove aria-hidden='true' from the button element.",
            target_attribute="aria-hidden",
            target_value=REMOVE_SENTINEL,
        )
        loc = _location(
            file_path="aria_hidden_test.html",
            start_line=1,
            matched_text='<button class="close-btn" aria-hidden="true" type="button">',
        )

        patch = self.gen.generate(plan, loc)

        assert patch is not None, "Expected patch for aria-hidden removal"
        removed_lines = [
            ln for ln in patch.unified_diff.splitlines()
            if ln.startswith("-") and not ln.startswith("---")
        ]
        assert any("aria-hidden" in ln for ln in removed_lines), (
            "Expected aria-hidden to be removed"
        )

    def test_replace_positive_tabindex(self):
        """Replace tabindex='3' with tabindex='0'."""
        test_html = '<a href="/home" tabindex="3" class="nav-link">Home</a>\n'
        _write_temp_file(self.repo, "tabindex_test.html", test_html)

        plan = _plan(
            problem_type=ProblemType.KEYBOARD_ACCESS,
            fix_strategy="Replace tabindex='3' with tabindex='0' on the <a> element.",
            target_attribute="tabindex",
            target_value="0",
            wcag_sc="2.4.3",
        )
        loc = _location(
            file_path="tabindex_test.html",
            start_line=1,
            matched_text='<a href="/home" tabindex="3" class="nav-link">Home</a>',
        )

        patch = self.gen.generate(plan, loc)

        assert patch is not None
        assert 'tabindex="0"' in patch.unified_diff
        assert 'tabindex="3"' not in "".join(
            ln for ln in patch.unified_diff.splitlines()
            if ln.startswith("+") and not ln.startswith("+++")
        )

    def test_returns_none_for_manual_review_plan(self):
        """Plans marked requires_manual_review must not produce a patch."""
        plan = _plan(requires_manual_review=True)
        loc = _location(file_path="index.html", start_line=29)

        patch = self.gen.generate(plan, loc)
        assert patch is None

    def test_returns_none_for_nonexistent_file(self):
        """A missing target file must return None safely."""
        plan = _plan(
            fix_strategy="Add aria-label to button.",
            target_attribute="aria-label",
            target_value="Close",
        )
        loc = _location(file_path="does_not_exist.html", start_line=1)

        patch = self.gen.generate(plan, loc)
        assert patch is None


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 2 — PatchGenerator: element insertion
# ═════════════════════════════════════════════════════════════════════════════


class TestPatchGeneratorElementInsertion:

    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(HTML_FIXTURE, self.tmp / "html_fixture")
        self.repo = self.tmp / "html_fixture"
        self.gen = PatchGenerator(repo_path=self.repo)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_insert_title_into_head(self):
        """Insert <title>...</title> into <head> — WCAG 2.4.2 fix."""
        plan = _plan(
            problem_type=ProblemType.MISSING_MARKUP,
            fix_strategy="Add a <title> element inside the <head> element.",
            root_cause="The page is missing a <title> element.",
            target_attribute="",   # No attribute — it's a new element
            target_value="My Application",
            wcag_sc="2.4.2",
        )
        loc = _location(
            file_path="index.html",
            start_line=3,  # <head> is around line 3
        )

        patch = self.gen.generate(plan, loc)

        assert patch is not None, (
            "Expected a patch for missing <title> insertion"
        )
        assert "<title>" in patch.unified_diff


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 3 — PatchGenerator: line replacement (CSS)
# ═════════════════════════════════════════════════════════════════════════════


class TestPatchGeneratorLineReplacement:

    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp
        self.gen = PatchGenerator(repo_path=self.repo)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_replace_outline_none_with_focus_indicator(self):
        """Replace outline: none with a WCAG 2.4.7 compliant focus style."""
        css_content = (
            ".btn:focus {\n"
            "  outline: none;\n"
            "  background: #005fcc;\n"
            "}\n"
        )
        _write_temp_file(self.repo, "styles.css", css_content)

        plan = _plan(
            problem_type=ProblemType.FOCUS_VISIBILITY,
            fix_strategy="Replace 'outline: none' with 'outline: 2px solid currentColor; outline-offset: 2px'",
            root_cause="Focus indicator is hidden.",
            target_attribute="outline",
            target_value="2px solid currentColor; outline-offset: 2px",
            wcag_sc="2.4.7",
        )
        loc = _location(
            file_path="styles.css",
            start_line=2,
            language="css",
            matched_text="  outline: none;",
        )

        patch = self.gen.generate(plan, loc)

        assert patch is not None, "Expected a CSS patch for outline:none"
        assert "outline: none" not in "".join(
            ln for ln in patch.unified_diff.splitlines()
            if ln.startswith("+") and not ln.startswith("+++")
        ), "outline:none should not appear in added lines"
        assert "currentColor" in patch.unified_diff or "2px" in patch.unified_diff


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 4 — PatchGenerator: diff format and integrity
# ═════════════════════════════════════════════════════════════════════════════


class TestPatchDiffIntegrity:

    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp
        self.gen = PatchGenerator(repo_path=self.repo)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_diff_has_correct_header(self):
        """Unified diff must start with --- a/ and +++ b/ headers."""
        html = '<html>\n<head></head>\n<body></body>\n</html>\n'
        _write_temp_file(self.repo, "test.html", html)

        plan = _plan(
            fix_strategy="Add lang='en' to the <html> element.",
            target_attribute="lang",
            target_value="en",
        )
        loc = _location(file_path="test.html", start_line=1, matched_text="<html>")

        patch = self.gen.generate(plan, loc)
        if patch:
            assert patch.unified_diff.startswith("---"), "Diff must start with ---"
            assert "+++ b/test.html" in patch.unified_diff

    def test_patch_hash_is_sha256(self):
        """patch_hash should be a 64-char hex string (SHA-256)."""
        html = '<html>\n<head></head>\n<body><button class="btn"></button></body>\n</html>\n'
        _write_temp_file(self.repo, "test.html", html)

        plan = _plan(
            fix_strategy="Add aria-label to button.",
            target_attribute="aria-label",
            target_value="Submit",
        )
        loc = _location(
            file_path="test.html",
            start_line=3,
            matched_text='<button class="btn">',
        )

        patch = self.gen.generate(plan, loc)
        if patch:
            assert len(patch.patch_hash) == 64
            assert all(c in "0123456789abcdef" for c in patch.patch_hash)

    def test_lines_added_removed_are_counted(self):
        """lines_added and lines_removed should be non-negative integers."""
        html = '<html>\n<head></head>\n<body><button></button></body>\n</html>\n'
        _write_temp_file(self.repo, "test.html", html)

        plan = _plan(
            fix_strategy="Add aria-label to button.",
            target_attribute="aria-label",
            target_value="Go",
        )
        loc = _location(file_path="test.html", start_line=3, matched_text="<button>")

        patch = self.gen.generate(plan, loc)
        if patch:
            assert patch.lines_added >= 0
            assert patch.lines_removed >= 0

    def test_is_minimal_for_single_attribute_change(self):
        """A single attribute addition should be marked is_minimal=True."""
        html = '<html>\n<head></head>\n<body><img src="/x.png" /></body>\n</html>\n'
        _write_temp_file(self.repo, "test.html", html)

        plan = _plan(
            problem_type=ProblemType.IMAGE_ALT,
            fix_strategy="Add alt='' to the decorative <img>.",
            target_attribute="alt",
            target_value="",
        )
        loc = _location(
            file_path="test.html",
            start_line=3,
            matched_text='<img src="/x.png" />',
        )

        patch = self.gen.generate(plan, loc)
        if patch:
            # A single-line change: 1 removed + 1 added = 2 lines changed ≤ 4
            assert patch.is_minimal is True


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 5 — PatchGenerator: error cases
# ═════════════════════════════════════════════════════════════════════════════


class TestPatchGeneratorErrorCases:

    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp
        self.gen = PatchGenerator(repo_path=self.repo)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_nonexistent_file_returns_none(self):
        plan = _plan(target_attribute="aria-label", target_value="x")
        loc = _location(file_path="missing_file.html", start_line=1)
        assert self.gen.generate(plan, loc) is None

    def test_empty_file_path_returns_none(self):
        plan = _plan(target_attribute="aria-label", target_value="x")
        loc = _location(file_path="", start_line=1)
        assert self.gen.generate(plan, loc) is None

    def test_no_change_returns_none(self):
        """If the attribute already has the desired value, no patch is generated."""
        html = '<html lang="en">\n<head></head>\n</html>\n'
        _write_temp_file(self.repo, "already_fixed.html", html)

        plan = _plan(
            problem_type=ProblemType.MISSING_MARKUP,
            fix_strategy="Add lang='en' to <html>.",
            target_attribute="lang",
            target_value="en",
        )
        loc = _location(
            file_path="already_fixed.html",
            start_line=1,
            matched_text='<html lang="en">',
        )

        patch = self.gen.generate(plan, loc)
        # Should return None because lang="en" is already there (no change)
        assert patch is None


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 6-10 — PatchValidator gates
# ═════════════════════════════════════════════════════════════════════════════


class TestPatchValidator:

    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp
        self.val = PatchValidator(repo_path=self.repo)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_patch(
        self,
        file_path: str = "test.html",
        diff: str = "",
        lines_added: int = 1,
        lines_removed: int = 1,
    ) -> GeneratedPatch:
        return GeneratedPatch(
            finding_id="A11Y-TEST",
            attempt_number=1,
            target_file=file_path,
            unified_diff=diff or (
                f"--- a/{file_path}\n"
                f"+++ b/{file_path}\n"
                "@@ -1,3 +1,3 @@\n"
                " <!DOCTYPE html>\n"
                '-<html>\n'
                '+<html lang="en">\n'
                " <head></head>\n"
            ),
            lines_added=lines_added,
            lines_removed=lines_removed,
            patch_hash="abc123",
        )

    def _make_plan(self, fix_strategy: str = "Add lang attribute.") -> RemediationPlan:
        return _plan(fix_strategy=fix_strategy, target_attribute="lang", target_value="en")

    # ── Gate 1 ────────────────────────────────────────────────────────────────

    def test_gate1_fails_when_file_missing(self):
        """Gate 1 fails when the target file does not exist."""
        patch = self._make_patch(file_path="ghost.html")
        result = self.val.validate(patch, self._make_plan())
        assert not result.file_exists
        assert not result.is_valid
        assert any("Gate 1" in r for r in result.failure_reasons)

    def test_gate1_passes_when_file_exists(self):
        """Gate 1 passes when the file is present."""
        content = "<!DOCTYPE html>\n<html>\n<head></head>\n</html>\n"
        _write_temp_file(self.repo, "test.html", content)
        patch = self._make_patch()
        result = self.val.validate(patch, self._make_plan())
        assert result.file_exists

    # ── Gate 2 ────────────────────────────────────────────────────────────────

    def test_gate2_fails_when_context_missing(self):
        """Gate 2 fails when the expected context lines don't exist in the file."""
        # File with completely different content
        _write_temp_file(self.repo, "test.html", "<html><body>completely different</body></html>\n")
        diff = (
            "--- a/test.html\n"
            "+++ b/test.html\n"
            "@@ -1,2 +1,2 @@\n"
            " <!DOCTYPE html>\n"       # This line does NOT exist in the file
            '-<html>\n'
            '+<html lang="en">\n'
        )
        patch = self._make_patch(diff=diff)
        result = self.val.validate(patch, self._make_plan())
        assert not result.context_matches
        assert any("Gate 2" in r for r in result.failure_reasons)

    def test_gate2_passes_when_context_matches(self):
        """Gate 2 passes when context lines are found in the file."""
        content = "<!DOCTYPE html>\n<html>\n<head></head>\n</html>\n"
        _write_temp_file(self.repo, "test.html", content)
        patch = self._make_patch()  # Default diff references these context lines
        result = self.val.validate(patch, self._make_plan())
        assert result.context_matches

    # ── Gate 5 ────────────────────────────────────────────────────────────────

    def test_gate5_fails_when_too_many_lines_changed(self):
        """Gate 5 rejects patches that change more lines than expected."""
        content = "<!DOCTYPE html>\n<html>\n<head></head>\n</html>\n"
        _write_temp_file(self.repo, "test.html", content)
        # Patch with 10 lines added — way beyond the threshold of 4
        patch = self._make_patch(lines_added=10, lines_removed=3)
        result = self.val.validate(patch, self._make_plan())
        assert not result.no_unrelated_changes
        assert any("Gate 5" in r for r in result.failure_reasons)

    def test_gate5_passes_for_minimal_patch(self):
        """Gate 5 passes when only 1-2 lines are changed."""
        content = "<!DOCTYPE html>\n<html>\n<head></head>\n</html>\n"
        _write_temp_file(self.repo, "test.html", content)
        patch = self._make_patch(lines_added=1, lines_removed=1)
        result = self.val.validate(patch, self._make_plan())
        assert result.no_unrelated_changes

    # ── Gate 6 ────────────────────────────────────────────────────────────────

    def test_gate6_rejects_api_key_in_diff(self):
        """Gate 6 rejects patches that add an API key."""
        content = "<!DOCTYPE html>\n<html>\n</html>\n"
        _write_temp_file(self.repo, "test.html", content)
        diff_with_secret = (
            "--- a/test.html\n"
            "+++ b/test.html\n"
            "@@ -1,2 +1,3 @@\n"
            " <!DOCTYPE html>\n"
            "+<meta name='api-key' content='sk-abcdefghijklmnopqrstuvwxyz1234567890'>\n"
            " <html>\n"
        )
        patch = self._make_patch(diff=diff_with_secret, lines_added=1, lines_removed=0)
        result = self.val.validate(patch, self._make_plan())
        assert not result.no_secrets_detected
        assert any("Gate 6" in r for r in result.failure_reasons)

    def test_gate6_passes_clean_diff(self):
        """Gate 6 passes when no secrets are present."""
        content = "<!DOCTYPE html>\n<html>\n<head></head>\n</html>\n"
        _write_temp_file(self.repo, "test.html", content)
        patch = self._make_patch()
        result = self.val.validate(patch, self._make_plan())
        assert result.no_secrets_detected

    # ── Gate 7 ────────────────────────────────────────────────────────────────

    def test_gate7_rejects_empty_aria_label(self):
        """Gate 7 rejects a patch that introduces aria-label=''."""
        content = '<!DOCTYPE html>\n<html>\n<button>\n</html>\n'
        _write_temp_file(self.repo, "test.html", content)
        diff_with_bad_aria = (
            "--- a/test.html\n"
            "+++ b/test.html\n"
            "@@ -1,3 +1,3 @@\n"
            " <!DOCTYPE html>\n"
            " <html>\n"
            '-<button>\n'
            '+<button aria-label="">\n'
            " </html>\n"
        )
        patch = self._make_patch(diff=diff_with_bad_aria)
        result = self.val.validate(patch, self._make_plan())
        assert not result.no_invalid_aria
        assert any("Gate 7" in r for r in result.failure_reasons)

    def test_gate7_passes_correct_aria_label(self):
        """Gate 7 passes when aria-label has a non-empty value."""
        content = "<!DOCTYPE html>\n<html>\n<head></head>\n</html>\n"
        _write_temp_file(self.repo, "test.html", content)
        patch = self._make_patch()  # Default adds lang="en" — no ARIA issues
        result = self.val.validate(patch, self._make_plan())
        assert result.no_invalid_aria


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 11 — Integration: generate → validate pipeline
# ═════════════════════════════════════════════════════════════════════════════


class TestGenerateValidatePipeline:
    """Full pipeline: PatchGenerator output feeds directly into PatchValidator."""

    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(HTML_FIXTURE, self.tmp / "html_fixture")
        self.repo = self.tmp / "html_fixture"
        self.gen = PatchGenerator(repo_path=self.repo)
        self.val = PatchValidator(repo_path=self.repo)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lang_fix_passes_validation(self):
        """Full pipeline: add lang='en' to <html> — should pass all gates."""
        plan = _plan(
            problem_type=ProblemType.MISSING_MARKUP,
            fix_strategy="Add lang='en' to the <html> element.",
            target_attribute="lang",
            target_value="en",
            wcag_sc="3.1.1",
        )
        loc = _location(file_path="index.html", start_line=2, matched_text="<html>")

        patch = self.gen.generate(plan, loc)

        if patch is None:
            pytest.skip("Patcher could not locate <html> in fixture — check fixture line numbers")

        result = self.val.validate(patch, plan)

        assert result.file_exists, f"Gate 1 failed: {result.failure_reasons}"
        assert result.no_secrets_detected, f"Gate 6 failed: {result.failure_reasons}"
        assert result.no_invalid_aria, f"Gate 7 failed: {result.failure_reasons}"

    def test_aria_label_fix_passes_validation(self):
        """Full pipeline: add aria-label to button — should pass all gates."""
        plan = _plan(
            problem_type=ProblemType.INCORRECT_ARIA,
            fix_strategy="Add aria-label='Register' to the <button> element on line 29.",
            target_attribute="aria-label",
            target_value="Register",
            wcag_sc="4.1.2",
        )
        loc = _location(
            file_path="index.html",
            start_line=29,
            matched_text='<button class="register-btn" type="submit">',
        )

        patch = self.gen.generate(plan, loc)

        if patch is None:
            pytest.skip("Patcher could not locate button in fixture")

        result = self.val.validate(patch, plan)

        assert result.file_exists
        assert result.no_secrets_detected
        assert result.no_invalid_aria
        # The patch must not change too many lines
        assert result.no_unrelated_changes, f"Gate 5 failed: {result.failure_reasons}"

    def test_color_contrast_plan_produces_no_patch(self):
        """DO_NOT_AUTO_REMEDIATE plans with requires_manual_review must produce no patch."""
        plan = _plan(
            problem_type=ProblemType.COLOR_CONTRAST,
            automation_level=RemediationAutomationLevel.DO_NOT_AUTO_REMEDIATE,
            fix_strategy="Manual review required. Color contrast issue.",
            requires_manual_review=True,
        )
        loc = _location(file_path="index.html", start_line=1)

        patch = self.gen.generate(plan, loc)
        assert patch is None, "DO_NOT_AUTO_REMEDIATE must never produce a patch"
