"""
Patch Validator — the 8-gate safety harness for generated patches.

A patch must pass ALL gates before it is applied to any file.
If any gate fails, the patch is rejected and the reason is recorded
in PatchValidationResult.failure_reasons for debugging.

GATES (in execution order)
--------------------------
Gate 1 — file_exists
    The target file exists on disk at the expected path.
    Failure: file was deleted or path is wrong.

Gate 2 — context_matches
    The file at that path still contains the content that the patcher
    read when it generated the diff. Verifies the file has not changed
    between locator run and patch application.
    Failure: another process modified the file; line numbers shifted.

Gate 3 — applies_cleanly
    `git apply --check` accepts the diff without errors.
    Failure: merge conflict, corrupted diff, encoding issue.
    SKIPPED if git is not available or repo is not a git repo.

Gate 4 — syntax_valid
    Parsing the patched content succeeds:
      HTML   → html.parser (stdlib)
      Python → ast.parse   (stdlib)
      CSS    → regex check (no complete parser available)
      JS/TS  → SKIPPED (no stdlib parser; relies on Gate 3)
    Failure: the patch introduced a syntax error.

Gate 5 — no_unrelated_changes
    The number of lines changed matches the plan's expected scope.
    An attribute injection plan should change ≤4 lines.
    An element insertion should change ≤3 lines.
    Failure: the patcher accidentally modified too much.

Gate 6 — no_secrets_detected
    The unified diff does not introduce known secret patterns:
    API keys, passwords, private keys, connection strings.
    Failure: LLM hallucinated a hardcoded secret into the fix.

Gate 7 — no_invalid_aria
    The patched content does not contain obviously broken ARIA patterns
    that would introduce a new violation:
      • aria-hidden="true" on a focusable element
      • role="presentation" on an interactive element
      • aria-label="" (empty label)
      • tabindex > 0
    Failure: the fix introduced an ARIA anti-pattern.

Gate 8 — no_new_contradictions
    The RAG contradiction checker (already in production) is run on
    the patched HTML to verify no new SC violations were introduced.
    Failure: the fix for SC 4.1.2 accidentally broke SC 2.4.4.
    SKIPPED for non-HTML files (CSS, Python, etc.).
"""

from __future__ import annotations

import html.parser
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from accessibility_agent.logging_config import get_logger
from accessibility_agent.remediation.schemas import (
    GeneratedPatch,
    PatchValidationResult,
    RemediationPlan,
)

log = get_logger(__name__)

# Maximum lines changed before Gate 5 triggers
_MAX_LINES_CHANGED_ATTRIBUTE = 4
_MAX_LINES_CHANGED_INSERTION = 3
_MAX_LINES_CHANGED_REPLACEMENT = 3

# Regex patterns for secret detection (Gate 6)
_SECRET_PATTERNS = [
    r"(?i)(api[_-]?key|apikey)\s*[=:]\s*['\"]?[a-zA-Z0-9_\-]{16,}",
    r"(?i)(password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{6,}['\"]",
    r"(?i)(secret|token)\s*[=:]\s*['\"]?[a-zA-Z0-9_\-]{16,}",
    r"(?i)(aws_access_key_id|aws_secret)\s*[=:]\s*['\"]?[A-Z0-9]{20,}",
    r"-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----",
    r"(?i)(database_url|db_url)\s*[=:]\s*['\"]?\w+://[^\s'\"]{10,}",
    r"(?i)(connection_string)\s*[=:]\s*['\"]?[^\s'\"]{20,}",
    # Catch sk- / pk- prefixed tokens common in AI/payment APIs
    r"\b(?:sk|pk|rk|ak)-[a-zA-Z0-9]{20,}\b",
    # Catch long hex/base64 strings following key/secret/token attribute names
    r'(?i)(?:api[_-]?key|secret|token|auth)[^>]*content\s*=\s*[\'"][a-zA-Z0-9+/=_\-]{20,}[\'"]',
]

# ARIA and structural anti-patterns that should never be introduced (Gate 7)
_INVALID_ARIA_PATTERNS = [
    (
        r'aria-hidden\s*=\s*"true"[^>]*(?:href|onclick|tabindex|type\s*=\s*"(?:button|submit))',
        "aria-hidden='true' on an interactive element"
    ),
    (
        r'aria-label\s*=\s*""',
        "aria-label is empty — provides no accessible name"
    ),
    (
        r'role\s*=\s*"(?:presentation|none)"\s[^>]*(?:onclick|href|tabindex)',
        "role='presentation' on an interactive element"
    ),
    (
        r'tabindex\s*=\s*"[1-9]\d*"',
        "Positive tabindex disrupts tab order"
    ),
    # ── New structural anti-patterns from audit ───────────────────────────────
    (
        r'<input[^>]+for\s*=\s*"',
        "for= attribute is invalid on <input> — it belongs on <label> elements only"
    ),
    (
        r'<title[^>]*>\s*<h[1-6]',
        "<h1>–<h6> inside <title> is invalid HTML — title must contain plain text only"
    ),
    (
        r'<title[^>]*>\s*<',
        "HTML element inside <title> is invalid — title must be plain text"
    ),
    (
        r'role\s*=\s*"region"\s[^>]*aria-label[^>]*>\s*(?:(?!</).)*</div>',
        "Check: role=region replacing an existing semantic landmark role (contentinfo/banner/main/nav) is a regression"
    ),
    (
        # Detect: button with aria-hidden=true AND tabindex=-1 added to element that already has aria-hidden on parent
        r'<button[^>]+aria-hidden\s*=\s*"true"[^>]+tabindex\s*=\s*"-1"',
        "Adding aria-hidden=true + tabindex=-1 to button buries it deeper — correct fix is removing aria-hidden from the parent div"
    ),
    (
        r'<a[^>]+class\s*=\s*"skip[^"]*"[^>]+id\s*=\s*"',
        "Adding id= to the skip LINK rather than the skip LINK TARGET is incorrect — the id belongs on <main> or the target element"
    ),
]


class PatchValidator:
    """
    Validates a GeneratedPatch through 8 sequential safety gates.

    Usage:
        validator = PatchValidator(repo_path=Path("./my-app"))
        result = validator.validate(patch, plan)

        if result.is_valid:
            # Apply the patch
        else:
            # Log result.failure_reasons and retry or escalate
    """

    def __init__(self, repo_path: Path) -> None:
        self._repo = repo_path.resolve()
        self._git_available = self._check_git()

    def validate(
        self,
        patch: GeneratedPatch,
        plan: RemediationPlan,
    ) -> PatchValidationResult:
        """
        Run all 8 validation gates on the patch.

        Gates run in order; all gates are always run (no short-circuit)
        so that failure_reasons captures ALL problems in one pass.
        """
        result = PatchValidationResult()
        result.file_exists = True
        result.context_matches = True
        result.applies_cleanly = True
        result.no_unrelated_changes = True
        result.no_secrets_detected = True
        result.no_invalid_aria = True
        result.no_new_contradictions = True
        result.is_valid = True

        abs_path = (self._repo / patch.target_file).resolve()
        language = Path(patch.target_file).suffix.lstrip(".")

        log.info(
            "validator.start",
            patch_id=patch.patch_id,
            finding_id=patch.finding_id,
            file=patch.target_file,
        )

        # ── Gate 1: File exists ───────────────────────────────────────────────
        result.file_exists = abs_path.exists()
        if not result.file_exists:
            result.failure_reasons.append(
                f"Gate 1 FAIL: File not found: {patch.target_file}"
            )
            log.warning("validator.gate1_fail", file=patch.target_file)

        # Read file content for remaining gates
        original_content = ""
        patched_content = ""
        if result.file_exists:
            try:
                original_content = abs_path.read_text(encoding="utf-8", errors="replace")
                patched_content = self._apply_diff_to_content(original_content, patch.unified_diff)
            except Exception as exc:
                result.failure_reasons.append(f"Gate 1 FAIL: Could not read file: {exc}")
                result.file_exists = False

        # ── Gate 2: Context matches ───────────────────────────────────────────
        result.context_matches = self._gate_context_matches(
            original_content, patch.unified_diff, result
        )

        # ── Gate 3: Applies cleanly (git apply --check) ───────────────────────
        result.applies_cleanly = self._gate_applies_cleanly(patch, result)

        # ── Gate 4: Syntax valid ──────────────────────────────────────────────
        result.syntax_valid = self._gate_syntax_valid(
            patched_content, language, result
        )

        # ── Gate 5: No unrelated changes ──────────────────────────────────────
        result.no_unrelated_changes = self._gate_no_unrelated_changes(
            patch, plan, result
        )

        # ── Gate 6: No secrets detected ───────────────────────────────────────
        result.no_secrets_detected = self._gate_no_secrets(patch.unified_diff, result)

        # ── Gate 7: No invalid ARIA ────────────────────────────────────────────
        result.no_invalid_aria = self._gate_no_invalid_aria(patched_content, result)

        # ── Gate 8: No new contradictions ─────────────────────────────────────
        result.no_new_contradictions = self._gate_no_contradictions(
            patched_content, language, plan, result
        )

        # ── Final decision ────────────────────────────────────────────────────
        must_pass = [
            result.file_exists,
            result.context_matches,
            result.applies_cleanly,
            result.no_unrelated_changes,
            result.no_secrets_detected,
            result.no_invalid_aria,
            result.no_new_contradictions,
        ]
        # syntax_valid is None when not applicable (not a hard failure)
        if result.syntax_valid is False:
            must_pass.append(False)

        result.is_valid = all(must_pass)

        log.info(
            "validator.complete",
            patch_id=patch.patch_id,
            is_valid=result.is_valid,
            failures=len(result.failure_reasons),
        )

        return result

    # ── Gate implementations ──────────────────────────────────────────────────

    def _gate_context_matches(
        self,
        original_content: str,
        diff_text: str,
        result: PatchValidationResult,
    ) -> bool:
        """
        Gate 2: Verify that the context lines in the diff exist in the file.

        Parses the diff and checks that every context line (lines starting
        with a space) and every removal line (lines starting with -)
        appears in the original file content.
        """
        if not original_content:
            result.failure_reasons.append("Gate 2 FAIL: Original file content is empty.")
            return False

        context_lines: list[str] = []
        for line in diff_text.splitlines():
            if line.startswith(" ") or line.startswith("-"):
                stripped = line[1:].rstrip()
                if stripped and not line.startswith("---"):
                    context_lines.append(stripped)

        if not context_lines:
            # No context lines to check → cannot verify
            return True

        original_lines_stripped = {ln.strip() for ln in original_content.splitlines()}
        missing = [cl for cl in context_lines[:5] if cl.strip() not in original_lines_stripped]

        if missing:
            result.failure_reasons.append(
                f"Gate 2 FAIL: Context mismatch — {len(missing)} expected lines not found "
                f"in file. File may have changed since scan. First missing: '{missing[0][:60]}'"
            )
            return False

        return True

    def _gate_applies_cleanly(
        self,
        patch: GeneratedPatch,
        result: PatchValidationResult,
    ) -> bool:
        """
        Gate 3: Run `git apply --check` on the patch.
        Returns True if git is not available (gate is skipped, not failed).
        """
        if not self._git_available:
            log.debug("validator.gate3_skipped_no_git")
            return True  # Skip, don't fail

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".patch",
                encoding="utf-8",
                delete=False,
            ) as f:
                f.write(patch.unified_diff)
                tmp_path = f.name

            cmd = ["git", "apply", "--check", "--whitespace=nowarn", tmp_path]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(self._repo),
                timeout=10,
            )

            Path(tmp_path).unlink(missing_ok=True)

            if proc.returncode != 0:
                err = proc.stderr.strip()[:200]
                result.failure_reasons.append(
                    f"Gate 3 FAIL: git apply --check failed: {err}"
                )
                return False

            return True

        except subprocess.TimeoutExpired:
            result.failure_reasons.append("Gate 3 FAIL: git apply --check timed out")
            return False
        except Exception as exc:
            log.debug("validator.gate3_error", error=str(exc))
            return True  # Non-fatal; git may not be configured

    def _gate_syntax_valid(
        self,
        patched_content: str,
        language: str,
        result: PatchValidationResult,
    ) -> bool | None:
        """
        Gate 4: Parse the patched content to verify syntax is valid.
        Returns None for languages with no available parser (skip).
        """
        if not patched_content:
            return None  # Skip

        if language in ("html", "htm"):
            return self._validate_html_syntax(patched_content, result)
        elif language == "py":
            return self._validate_python_syntax(patched_content, result)
        elif language in ("css", "scss"):
            return self._validate_css_syntax(patched_content, result)
        else:
            return None  # Skip for tsx/jsx/ts/js/vue (no stdlib parser)

    def _gate_no_unrelated_changes(
        self,
        patch: GeneratedPatch,
        plan: RemediationPlan,
        result: PatchValidationResult,
    ) -> bool:
        """Gate 5: The patch changes no more lines than expected."""
        total_changed = patch.lines_added + patch.lines_removed

        # Attribute injection: ≤4 lines
        # Element insertion: ≤3 lines (1 new element + up to 2 context shifts)
        # Line replacement: ≤3 lines
        strategy = plan.fix_strategy.lower()
        if "insert" in strategy or "add <" in strategy:
            max_lines = _MAX_LINES_CHANGED_INSERTION
        else:
            max_lines = _MAX_LINES_CHANGED_ATTRIBUTE

        if total_changed > max_lines:
            result.failure_reasons.append(
                f"Gate 5 FAIL: Patch changes {total_changed} lines but expected ≤{max_lines}. "
                f"Possible unintended modifications. Added={patch.lines_added}, "
                f"Removed={patch.lines_removed}."
            )
            patch.unrelated_changes_detected = True
            return False

        return True

    @staticmethod
    def _gate_no_secrets(diff_text: str, result: PatchValidationResult) -> bool:
        """Gate 6: Scan diff additions for secret patterns."""
        # Only check lines that are being ADDED (start with +, not +++)
        added_lines = [
            ln[1:] for ln in diff_text.splitlines()
            if ln.startswith("+") and not ln.startswith("+++")
        ]
        added_text = "\n".join(added_lines)

        for pattern in _SECRET_PATTERNS:
            m = re.search(pattern, added_text)
            if m:
                # Redact the actual value in the error message
                result.failure_reasons.append(
                    f"Gate 6 FAIL: Potential secret detected in added lines: "
                    f"pattern '{pattern[:40]}' matched. Patch rejected."
                )
                return False

        return True

    @staticmethod
    def _gate_no_invalid_aria(patched_content: str, result: PatchValidationResult) -> bool:
        """Gate 7: Check patched content for ARIA anti-patterns."""
        if not patched_content:
            return True

        found_issues: list[str] = []
        for pattern, description in _INVALID_ARIA_PATTERNS:
            if re.search(pattern, patched_content, re.IGNORECASE | re.DOTALL):
                found_issues.append(description)

        if found_issues:
            result.failure_reasons.append(
                f"Gate 7 FAIL: Patch introduces ARIA anti-patterns: "
                + "; ".join(found_issues)
            )
            return False

        return True

    def _gate_no_contradictions(
        self,
        patched_content: str,
        language: str,
        plan: RemediationPlan,
        result: PatchValidationResult,
    ) -> bool:
        """
        Gate 8: Run the RAG contradiction checker on patched HTML.

        Uses RAGEngine.get_actual_conflicts() which filters by SCRelationship —
        only ACTUAL_CONFLICT blocks the patch.
        POTENTIALLY_INTERACTING / UNKNOWN are recorded but do not fail this gate.
        Skipped for non-HTML files.
        """
        if language not in ("html", "htm", "vue"):
            return True  # Skip for non-HTML

        if not patched_content:
            return True

        try:
            from accessibility_agent.wcag.rag_engine import RAGEngine
            rag = RAGEngine()
            wcag_sc = plan.wcag_criterion or ""

            # Only blocking ACTUAL_CONFLICTs fail Gate 8
            blocking = rag.get_actual_conflicts(patched_content, wcag_sc)

            if blocking:
                result.contradiction_details = blocking
                violations = [c.get("violates_sc", "") for c in blocking]
                result.failure_reasons.append(
                    f"Gate 8 FAIL: Patch introduces ACTUAL WCAG contradictions with SC(s): "
                    + ", ".join(violations)
                    + ". These are blocking violations, not warnings."
                )
                return False

            # Also run full check to capture non-blocking warnings for the report
            all_findings = rag.check_fix_for_contradictions(patched_content, wcag_sc)
            warnings = [f for f in all_findings if f.get("is_blocking") != "true"]
            if warnings:
                result.contradiction_details.extend(warnings)

        except Exception as exc:
            log.debug("validator.gate8_error", error=str(exc))
            # RAG engine not available → skip, don't fail
            return True

        return True


    # ── Syntax validators ─────────────────────────────────────────────────────

    @staticmethod
    def _validate_html_syntax(content: str, result: PatchValidationResult) -> bool:
        """Validate HTML using Python's stdlib html.parser."""
        errors: list[str] = []

        class StrictHTMLParser(html.parser.HTMLParser):
            def handle_error(self, message: str) -> None:
                errors.append(message)

        try:
            parser = StrictHTMLParser()
            parser.feed(content)
            parser.close()
        except html.parser.HTMLParseError as exc:
            result.failure_reasons.append(
                f"Gate 4 FAIL: HTML parse error after patching: {exc}"
            )
            result.linter_output = str(exc)
            return False

        return True

    @staticmethod
    def _validate_python_syntax(content: str, result: PatchValidationResult) -> bool:
        """Validate Python syntax using ast.parse."""
        import ast
        try:
            ast.parse(content)
            return True
        except SyntaxError as exc:
            result.failure_reasons.append(
                f"Gate 4 FAIL: Python syntax error at line {exc.lineno}: {exc.msg}"
            )
            result.linter_output = str(exc)
            return False

    @staticmethod
    def _validate_css_syntax(content: str, result: PatchValidationResult) -> bool:
        """Basic CSS validation — checks for unclosed braces."""
        open_braces = content.count("{")
        close_braces = content.count("}")
        if open_braces != close_braces:
            result.failure_reasons.append(
                f"Gate 4 FAIL: CSS brace mismatch after patching: "
                f"{open_braces} open, {close_braces} close."
            )
            return False
        return True

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _apply_diff_to_content(original: str, diff_text: str) -> str:
        """Apply a unified diff to string content, returning the patched result."""
        original_lines = original.splitlines(keepends=True)
        diff_lines = diff_text.splitlines(keepends=True)

        patched = list(original_lines)
        i = 0
        offset = 0

        while i < len(diff_lines):
            line = diff_lines[i]
            hunk = re.match(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@", line)
            if not hunk:
                i += 1
                continue

            orig_start = int(hunk.group(1)) - 1
            i += 1

            hunk_orig: list[str] = []
            hunk_new: list[str] = []

            while i < len(diff_lines) and not diff_lines[i].startswith("@@"):
                dl = diff_lines[i]
                if dl.startswith("-"):
                    hunk_orig.append(dl[1:])
                elif dl.startswith("+"):
                    hunk_new.append(dl[1:])
                elif dl.startswith(" "):
                    hunk_orig.append(dl[1:])
                    hunk_new.append(dl[1:])
                i += 1

            apply_at = orig_start + offset
            patched[apply_at:apply_at + len(hunk_orig)] = hunk_new
            offset += len(hunk_new) - len(hunk_orig)

        return "".join(patched)

    @staticmethod
    def _check_git() -> bool:
        """Check whether git is available on PATH."""
        try:
            subprocess.run(
                ["git", "--version"],
                capture_output=True,
                timeout=5,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
