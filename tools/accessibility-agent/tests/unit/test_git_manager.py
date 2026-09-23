"""
Unit tests for Phase 5: GitManager (git_manager.py)

All tests use dry_run=True or monkeypatch subprocess to avoid
touching real git or making network calls.

Test groups:
    1. Branch naming — deterministic, git-safe names
    2. Commit message building — conventional commits format
    3. PR title and body builders — content correctness
    4. Full workflow (dry_run=True) — stage flags correct
    5. Rollback behavior — cleans up on failure
    6. PR creation — payload structure and GitHub API call
    7. _git() subprocess wrapper — timeout, missing git, errors
    8. _detect_owner_repo() — HTTPS and SSH remote URL parsing
    9. GitWorkflowResult properties
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from accessibility_agent.remediation.git_manager import (
    GitManager,
    GitWorkflowResult,
    _BRANCH_PREFIX,
)
from accessibility_agent.remediation.schemas import (
    GeneratedPatch,
    PatchValidationResult,
    RemediationAutomationLevel,
    RemediationPlan,
    ProblemType,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_manager(
    tmp_path: Path,
    dry_run: bool = True,
    token: str = "test-token",
    base_branch: str = "main",
) -> GitManager:
    return GitManager(
        repo_path=tmp_path,
        base_branch=base_branch,
        github_token=token,
        dry_run=dry_run,
    )


def _make_patch(
    finding_id: str = "A11Y-001",
    target_file: str = "index.html",
    attempt: int = 1,
    lines_added: int = 1,
    lines_removed: int = 1,
) -> GeneratedPatch:
    return GeneratedPatch(
        finding_id=finding_id,
        attempt_number=attempt,
        target_file=target_file,
        unified_diff=(
            f"--- a/{target_file}\n"
            f"+++ b/{target_file}\n"
            "@@ -1,3 +1,3 @@\n"
            " <!DOCTYPE html>\n"
            '-<html>\n'
            '+<html lang="en">\n'
            " <head></head>\n"
        ),
        lines_added=lines_added,
        lines_removed=lines_removed,
        patch_hash="abc123def456",
    )


def _make_plan(
    finding_id: str = "A11Y-001",
    wcag_sc: str = "3.1.1",
    fix_strategy: str = "Add lang='en' to the <html> element.",
    root_cause: str = "The page is missing a lang attribute.",
    attempt: int = 1,
) -> RemediationPlan:
    return RemediationPlan(
        finding_id=finding_id,
        attempt_number=attempt,
        automation_level=RemediationAutomationLevel.SAFE_AUTO_FIX,
        classification_confidence=0.99,
        classified_by="deterministic_rules",
        problem_type=ProblemType.MISSING_MARKUP,
        root_cause=root_cause,
        fix_strategy=fix_strategy,
        target_attribute="lang",
        target_value="en",
        risk_level="low",
        wcag_criterion=wcag_sc,
        requires_manual_review=False,
    )


def _make_validation() -> PatchValidationResult:
    return PatchValidationResult(
        is_valid=True,
        file_exists=True,
        context_matches=True,
        applies_cleanly=True,
        syntax_valid=True,
        no_unrelated_changes=True,
        no_secrets_detected=True,
        no_invalid_aria=True,
        no_new_contradictions=True,
    )


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 1 — Branch naming
# ═════════════════════════════════════════════════════════════════════════════


class TestBranchNaming:

    def test_basic_finding_id(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        name = mgr._make_branch_name("A11Y-001", attempt=1)
        assert name.startswith(f"{_BRANCH_PREFIX}-")
        assert "attempt-1" in name
        assert "a11y-001" in name

    def test_special_chars_sanitized(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        name = mgr._make_branch_name("FIND/ID with spaces & stuff!", attempt=2)
        # Must not contain spaces, /, &, !
        assert " " not in name
        assert "/" not in name.split(_BRANCH_PREFIX)[1]  # Only prefix has /
        assert "&" not in name
        assert "!" not in name

    def test_uppercase_converted_to_lowercase(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        name = mgr._make_branch_name("BUTTON-NAME-MISSING", attempt=1)
        assert name == name.lower()

    def test_long_id_truncated(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        long_id = "A" * 200
        name = mgr._make_branch_name(long_id, attempt=1)
        # Total branch name must be reasonable
        assert len(name) < 120

    def test_attempt_number_in_name(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        assert "attempt-3" in mgr._make_branch_name("A11Y-001", attempt=3)
        assert "attempt-1" in mgr._make_branch_name("A11Y-001", attempt=1)

    def test_deterministic(self, tmp_path: Path):
        """Same inputs always produce the same branch name."""
        mgr = _make_manager(tmp_path)
        name1 = mgr._make_branch_name("A11Y-button-name", attempt=2)
        name2 = mgr._make_branch_name("A11Y-button-name", attempt=2)
        assert name1 == name2


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 2 — Commit message building
# ═════════════════════════════════════════════════════════════════════════════


class TestCommitMessageBuilding:

    def test_conventional_commit_prefix(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        patch = _make_patch()
        plan = _make_plan(wcag_sc="3.1.1")
        msg = mgr._build_commit_message(patch, plan)
        assert msg.startswith("fix(a11y):")

    def test_sc_in_title(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        msg = mgr._build_commit_message(_make_patch(), _make_plan(wcag_sc="4.1.2"))
        first_line = msg.splitlines()[0]
        assert "4.1.2" in first_line

    def test_finding_id_in_body(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        msg = mgr._build_commit_message(
            _make_patch(finding_id="A11Y-xyz-999"), _make_plan()
        )
        assert "A11Y-xyz-999" in msg

    def test_file_path_in_body(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        msg = mgr._build_commit_message(
            _make_patch(target_file="src/components/Nav.tsx"), _make_plan()
        )
        assert "src/components/Nav.tsx" in msg

    def test_lines_changed_in_body(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        msg = mgr._build_commit_message(
            _make_patch(lines_added=3, lines_removed=1), _make_plan()
        )
        assert "+3" in msg
        assert "-1" in msg

    def test_very_long_strategy_truncated(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        plan = _make_plan(fix_strategy="X" * 500)
        msg = mgr._build_commit_message(_make_patch(), plan)
        # Title line must be reasonable
        title = msg.splitlines()[0]
        assert len(title) <= 120

    def test_automated_signature_present(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        msg = mgr._build_commit_message(_make_patch(), _make_plan())
        assert "Auto-generated" in msg


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 3 — PR title and body builders
# ═════════════════════════════════════════════════════════════════════════════


class TestPRBuilders:

    def test_pr_title_contains_sc(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        plan = _make_plan(wcag_sc="2.4.2")
        title = mgr._build_pr_title(plan)
        assert "2.4.2" in title

    def test_pr_title_within_limit(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        plan = _make_plan(fix_strategy="A" * 500)
        title = mgr._build_pr_title(plan)
        assert len(title) <= 120

    def test_pr_body_contains_diff(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        patch = _make_patch()
        body = mgr._build_pr_body(patch, _make_plan(), _make_validation())
        assert "```diff" in body
        assert "+++ b/index.html" in body

    def test_pr_body_contains_finding_id(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        body = mgr._build_pr_body(
            _make_patch(finding_id="A11Y-TEST-007"), _make_plan(), _make_validation()
        )
        assert "A11Y-TEST-007" in body

    def test_pr_body_contains_all_gate_results(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        body = mgr._build_pr_body(_make_patch(), _make_plan(), _make_validation())
        assert "✅ File exists" in body
        assert "✅ No secrets" in body
        assert "✅ No invalid ARIA" in body

    def test_pr_body_shows_failed_gate(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        val = _make_validation()
        val.no_unrelated_changes = False
        body = mgr._build_pr_body(_make_patch(), _make_plan(), val)
        assert "❌ No unrelated changes" in body

    def test_pr_body_includes_non_blocking_warnings(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        val = _make_validation()
        val.contradiction_details = [
            {
                "violates_sc": "2.4.3",
                "message": "tabindex positive",
                "is_blocking": "false",
                "relationship": "potentially_interacting",
            }
        ]
        body = mgr._build_pr_body(_make_patch(), _make_plan(), val)
        assert "2.4.3" in body

    def test_pr_body_auto_generated_notice(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        body = mgr._build_pr_body(_make_patch(), _make_plan(), _make_validation())
        assert "Auto-generated" in body
        assert "Human review is required" in body


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 4 — Full workflow (dry_run=True)
# ═════════════════════════════════════════════════════════════════════════════


class TestFullWorkflowDryRun:
    """dry_run=True skips all subprocess/network calls but exercises the logic."""

    def test_all_stage_flags_set_on_success(self, tmp_path: Path):
        mgr = _make_manager(tmp_path, dry_run=True)
        result = mgr.full_workflow(_make_patch(), _make_plan(), _make_validation())

        assert result.branch_created
        assert result.patch_applied
        assert result.committed
        assert result.pushed
        assert not result.rolled_back
        assert not result.error

    def test_branch_name_in_result(self, tmp_path: Path):
        mgr = _make_manager(tmp_path, dry_run=True)
        result = mgr.full_workflow(
            _make_patch(finding_id="A11Y-button-name"), _make_plan(), _make_validation()
        )
        assert "a11y-button-name" in result.branch_name
        assert "attempt-1" in result.branch_name

    def test_pr_created_with_token(self, tmp_path: Path):
        mgr = _make_manager(tmp_path, dry_run=True, token="test-token")
        # Force _owner_repo so PR creation proceeds
        mgr._owner_repo = "owner/repo"
        result = mgr.full_workflow(_make_patch(), _make_plan(), _make_validation())
        assert result.pr_created
        assert "dry-run" in result.pr_url

    def test_pr_skipped_without_token(self, tmp_path: Path):
        mgr = _make_manager(tmp_path, dry_run=True, token="")
        result = mgr.full_workflow(_make_patch(), _make_plan(), _make_validation())
        assert not result.pr_created
        # But everything else succeeded
        assert result.committed

    def test_succeeded_property_true_on_success(self, tmp_path: Path):
        mgr = _make_manager(tmp_path, dry_run=True)
        result = mgr.full_workflow(_make_patch(), _make_plan(), _make_validation())
        assert result.succeeded

    def test_different_attempt_numbers_in_branch(self, tmp_path: Path):
        mgr = _make_manager(tmp_path, dry_run=True)
        r1 = mgr.full_workflow(_make_patch(attempt=1), _make_plan(attempt=1), _make_validation())
        r2 = mgr.full_workflow(_make_patch(attempt=2), _make_plan(attempt=2), _make_validation())
        assert r1.branch_name != r2.branch_name
        assert "attempt-1" in r1.branch_name
        assert "attempt-2" in r2.branch_name


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 5 — Rollback behavior
# ═════════════════════════════════════════════════════════════════════════════


class TestRollbackBehavior:

    def test_rollback_sets_rolled_back_flag(self, tmp_path: Path):
        mgr = _make_manager(tmp_path, dry_run=True)
        result = GitWorkflowResult(finding_id="A11Y-001", branch_name="a11y/fix-test")
        mgr._rollback("a11y/fix-test", result)
        assert result.rolled_back

    def test_succeeded_false_after_rollback(self, tmp_path: Path):
        result = GitWorkflowResult(finding_id="A11Y-001")
        result.committed = True
        result.rolled_back = True
        # Even if committed, succeeded=False when rolled_back=True
        assert not result.succeeded

    def test_succeeded_false_when_error(self, tmp_path: Path):
        result = GitWorkflowResult(finding_id="A11Y-001")
        result.committed = True
        result.error = "Something went wrong"
        assert not result.succeeded

    def test_workflow_calls_rollback_on_apply_failure(self, tmp_path: Path):
        """If patch application fails, rollback must be triggered."""
        mgr = _make_manager(tmp_path, dry_run=False)

        # Mock _create_branch to succeed
        mgr._create_branch = lambda branch, result: (
            setattr(result, "branch_created", True) or True
        )

        # Mock _apply_patch_to_disk to fail
        def fail_apply(patch, result):
            result.error = "apply failed"
            result.error_stage = "apply_patch"
            return False

        mgr._apply_patch_to_disk = fail_apply

        # Track rollback calls
        rollback_called = []
        original_rollback = mgr._rollback

        def tracking_rollback(branch, result):
            rollback_called.append(branch)
            result.rolled_back = True
            return True

        mgr._rollback = tracking_rollback

        patch = _make_patch()
        result = mgr.full_workflow(patch, _make_plan(), _make_validation())

        assert len(rollback_called) == 1, "Rollback must be called exactly once"
        assert result.rolled_back
        assert not result.succeeded


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 6 — PR creation payload
# ═════════════════════════════════════════════════════════════════════════════


class TestPRCreation:

    def test_pr_payload_has_required_fields(self, tmp_path: Path):
        """The PR payload sent to GitHub must have title, body, head, base."""
        mgr = _make_manager(tmp_path, dry_run=False, token="tok")
        mgr._owner_repo = "owner/repo"

        captured_payload: dict = {}

        def mock_urlopen(req, timeout=None):
            import json as _json
            body = req.data.decode("utf-8")
            captured_payload.update(_json.loads(body))
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "html_url": "https://github.com/owner/repo/pull/42",
                "number": 42,
            }).encode("utf-8")
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = GitWorkflowResult(finding_id="A11Y-001", branch_name="a11y/fix-test")
            mgr._create_pr(
                "a11y/fix-test",
                _make_patch(),
                _make_plan(),
                _make_validation(),
                result,
            )

        assert "title" in captured_payload
        assert "body" in captured_payload
        assert "head" in captured_payload
        assert "base" in captured_payload
        assert captured_payload["head"] == "a11y/fix-test"
        assert captured_payload["base"] == "main"

    def test_pr_result_fields_populated_on_success(self, tmp_path: Path):
        """PR URL and number must be set from the API response."""
        mgr = _make_manager(tmp_path, dry_run=False, token="tok")
        mgr._owner_repo = "owner/repo"

        def mock_urlopen(req, timeout=None):
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "html_url": "https://github.com/owner/repo/pull/99",
                "number": 99,
            }).encode("utf-8")
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = GitWorkflowResult(finding_id="A11Y-001", branch_name="a11y/fix-test")
            mgr._create_pr(
                "a11y/fix-test",
                _make_patch(),
                _make_plan(),
                _make_validation(),
                result,
            )

        assert result.pr_url == "https://github.com/owner/repo/pull/99"
        assert result.pr_number == 99
        assert result.pr_created

    def test_pr_creation_skipped_when_no_owner_repo(self, tmp_path: Path):
        """If owner/repo cannot be detected, PR creation is silently skipped."""
        mgr = _make_manager(tmp_path, dry_run=False, token="tok")
        mgr._owner_repo = ""  # No remote detected

        result = GitWorkflowResult(finding_id="A11Y-001", branch_name="a11y/fix-test")
        mgr._create_pr("a11y/fix-test", _make_patch(), _make_plan(), _make_validation(), result)

        assert not result.pr_created  # Skipped, not failed

    def test_pr_http_error_does_not_trigger_rollback(self, tmp_path: Path):
        """HTTP errors in PR creation must set error but NOT trigger rollback."""
        import urllib.error
        mgr = _make_manager(tmp_path, dry_run=False, token="tok")
        mgr._owner_repo = "owner/repo"

        def mock_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                url="", code=422, msg="Unprocessable", hdrs=None,
                fp=MagicMock(read=lambda: b'{"message": "PR already exists"}')
            )

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = GitWorkflowResult(finding_id="A11Y-001", branch_name="a11y/fix-test")
            mgr._create_pr(
                "a11y/fix-test", _make_patch(), _make_plan(), _make_validation(), result
            )

        assert not result.pr_created
        assert result.error  # Error recorded
        assert not result.rolled_back  # But NOT rolled back


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 7 — _git() subprocess wrapper
# ═════════════════════════════════════════════════════════════════════════════


class TestGitSubprocessWrapper:

    def test_returns_none_on_nonzero_exit(self, tmp_path: Path):
        mgr = _make_manager(tmp_path, dry_run=False)

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "error: not a git repository"

        with patch("subprocess.run", return_value=mock_proc):
            result = mgr._git(["status"])
        assert result is None

    def test_returns_stdout_on_success(self, tmp_path: Path):
        mgr = _make_manager(tmp_path, dry_run=False)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "main\n"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = mgr._git(["rev-parse", "--abbrev-ref", "HEAD"])
        assert result == "main\n"

    def test_returns_none_on_timeout(self, tmp_path: Path):
        mgr = _make_manager(tmp_path, dry_run=False)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30)):
            result = mgr._git(["fetch"])
        assert result is None

    def test_returns_none_when_git_not_found(self, tmp_path: Path):
        mgr = _make_manager(tmp_path, dry_run=False)

        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            result = mgr._git(["status"])
        assert result is None

    def test_check_false_returns_none_silently_on_failure(self, tmp_path: Path):
        """check=False should return None without logging an error."""
        mgr = _make_manager(tmp_path, dry_run=False)

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "no remote"

        with patch("subprocess.run", return_value=mock_proc):
            result = mgr._git(["pull", "origin", "main"], check=False)
        assert result is None


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 8 — Remote URL parsing
# ═════════════════════════════════════════════════════════════════════════════


class TestRemoteURLParsing:

    def _parse(self, url: str, tmp_path: Path) -> str:
        """Helper: parse a remote URL via _detect_owner_repo."""
        mgr = _make_manager(tmp_path, dry_run=False)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = url + "\n"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            return mgr._detect_owner_repo()

    def test_https_url(self, tmp_path: Path):
        result = self._parse("https://github.com/owner/repo.git", tmp_path)
        assert result == "owner/repo"

    def test_https_url_without_dot_git(self, tmp_path: Path):
        result = self._parse("https://github.com/owner/repo", tmp_path)
        assert result == "owner/repo"

    def test_ssh_url(self, tmp_path: Path):
        result = self._parse("git@github.com:owner/repo.git", tmp_path)
        assert result == "owner/repo"

    def test_non_github_url_returns_empty(self, tmp_path: Path):
        result = self._parse("https://gitlab.com/owner/repo.git", tmp_path)
        assert result == ""

    def test_empty_remote_returns_empty(self, tmp_path: Path):
        mgr = _make_manager(tmp_path, dry_run=False)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = mgr._detect_owner_repo()
        assert result == ""


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 9 — GitWorkflowResult properties
# ═════════════════════════════════════════════════════════════════════════════


class TestGitWorkflowResult:

    def test_succeeded_requires_committed_and_no_rollback_and_no_error(self):
        r = GitWorkflowResult(finding_id="A11Y-001")
        assert not r.succeeded  # Nothing set yet

        r.committed = True
        assert r.succeeded  # Now it should succeed

        r.rolled_back = True
        assert not r.succeeded  # Rolled back → not succeeded

        r.rolled_back = False
        r.error = "oops"
        assert not r.succeeded  # Error → not succeeded

    def test_initial_state(self):
        r = GitWorkflowResult(finding_id="A11Y-123")
        assert r.finding_id == "A11Y-123"
        assert r.branch_name == ""
        assert r.commit_sha == ""
        assert r.pr_url == ""
        assert r.pr_number == 0
        assert not r.branch_created
        assert not r.patch_applied
        assert not r.committed
        assert not r.pushed
        assert not r.pr_created
        assert not r.rolled_back
        assert r.error == ""
        assert r.error_stage == ""
