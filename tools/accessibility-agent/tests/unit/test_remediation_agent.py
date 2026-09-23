"""
Integration tests for Phase 8: RemediationAgent end-to-end orchestrator.

All external dependencies (SourceLocator, PatchGenerator, GitManager, AxeEngine, etc.)
are replaced with mocks so these tests run deterministically with zero network/git/browser calls.

Test groups:
    1. Happy path — verified fix, PR created
    2. DO_NOT_AUTO_REMEDIATE — manual review routing
    3. Retry loop — patch fails once, succeeds on retry
    4. All attempts exhausted — FAILED status
    5. Tests fail after patching — rollback
    6. No tests detected — warn but proceed
    7. Stage error handling — exceptions don't crash the agent
    8. RemediationAgentResult properties
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from accessibility_agent.remediation.agent import RemediationAgent, RemediationAgentResult
from accessibility_agent.remediation.schemas import (
    GeneratedPatch,
    PatchValidationResult,
    RemediationAutomationLevel,
    RemediationPlan,
    RemediationStatus,
    ProblemType,
)
from accessibility_agent.remediation.verifier import VerificationStatus, VerificationResult
from accessibility_agent.remediation.git_manager import GitWorkflowResult
from accessibility_agent.remediation.test_runner import TestRunResult, TestFramework
from accessibility_agent.remediation.locator import SourceLocation
from accessibility_agent.wcag.schemas import (
    Finding, ElementLocator, WCAGMapping, WCAGVersion, WCAGLevel, EvidenceItem
)


# ── Fixtures and helpers ──────────────────────────────────────────────────────

@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    return tmp_path


def _make_finding(finding_id: str = "A11Y-001", rule_id: str = "html-has-lang") -> Finding:
    wcag = WCAGMapping(
        version=WCAGVersion.V22, success_criterion="3.1.1", level=WCAGLevel.A,
        title="Language of Page", principle="Understandable"
    )
    evidence = EvidenceItem(evidence_type="tool_output", data="[]", description="Mock")
    return Finding(
        finding_id=finding_id,
        url="http://localhost/",
        rule_id=rule_id,
        wcag=wcag,
        status="confirmed",
        detection_method="automated",
        description="html element has no lang attribute",
        evidence=[evidence],
    )


def _make_source_location(file_path: str = "index.html") -> SourceLocation:
    return SourceLocation(
        file_path=file_path,
        start_line=1,
        end_line=1,
        language="html",
        confidence="direct_match",
        framework="unknown",
        matched_text='<html>',
    )


def _make_plan(finding_id: str = "A11Y-001", attempt: int = 1) -> RemediationPlan:
    return RemediationPlan(
        finding_id=finding_id,
        attempt_number=attempt,
        automation_level=RemediationAutomationLevel.SAFE_AUTO_FIX,
        classification_confidence=0.99,
        classified_by="deterministic_rules",
        problem_type=ProblemType.MISSING_MARKUP,
        root_cause="Missing lang attribute",
        fix_strategy="Add lang='en' to the html element.",
        target_attribute="lang",
        target_value="en",
        risk_level="low",
        wcag_criterion="3.1.1",
        requires_manual_review=False,
    )


def _make_patch(finding_id: str = "A11Y-001", attempt: int = 1) -> GeneratedPatch:
    return GeneratedPatch(
        finding_id=finding_id,
        attempt_number=attempt,
        target_file="index.html",
        unified_diff="--- a/index.html\n+++ b/index.html\n@@ -1 +1 @@\n-<html>\n+<html lang=\"en\">\n",
        lines_added=1,
        lines_removed=1,
        patch_hash="abc123",
    )


def _make_validation(is_valid: bool = True) -> PatchValidationResult:
    return PatchValidationResult(
        is_valid=is_valid,
        file_exists=is_valid,
        context_matches=is_valid,
        applies_cleanly=is_valid,
        syntax_valid=is_valid,
        no_unrelated_changes=is_valid,
        no_secrets_detected=is_valid,
        no_invalid_aria=is_valid,
        no_new_contradictions=is_valid,
    )


def _make_git_result(succeeded: bool = True) -> GitWorkflowResult:
    r = GitWorkflowResult(finding_id="A11Y-001", branch_name="a11y/fix-test-attempt-1")
    r.branch_created = succeeded
    r.patch_applied = succeeded
    r.committed = succeeded
    r.pushed = succeeded
    r.pr_url = "https://github.com/owner/repo/pull/42" if succeeded else ""
    r.pr_number = 42 if succeeded else 0
    r.pr_created = succeeded
    return r


def _make_test_result(success: bool = True, ran: bool = True) -> TestRunResult:
    return TestRunResult(
        framework=TestFramework.PYTEST if ran else TestFramework.UNKNOWN,
        ran_tests=ran,
        success=success,
        output="2 passed" if success else "",
        error="" if success else "1 test failed",
    )


def _make_verification(status: VerificationStatus = VerificationStatus.FIXED) -> VerificationResult:
    return VerificationResult(
        status=status,
        original_finding_id="A11Y-001",
        new_issues_introduced=[],
        message="Fixed",
    )


def _configure_agent_mocks(agent: RemediationAgent, **overrides):
    """
    Replace all agent sub-components with simple mocks.
    Pass keyword arguments to override individual mock return values.
    """
    agent._locator.locate = MagicMock(return_value=overrides.get("source_location", _make_source_location()))
    agent._analyzer.analyze = MagicMock(return_value=MagicMock())
    agent._classifier.classify = MagicMock(return_value=(
        overrides.get("automation_level", RemediationAutomationLevel.SAFE_AUTO_FIX),
        None, 0.99, "mock"
    ))
    agent._planner.plan = MagicMock(return_value=overrides.get("plan", _make_plan()))
    agent._patch_generator.generate = MagicMock(return_value=overrides.get("patch", _make_patch()))
    agent._patch_validator.validate = MagicMock(return_value=overrides.get("validation", _make_validation()))
    agent._git_manager.full_workflow = MagicMock(return_value=overrides.get("git_result", _make_git_result()))
    agent._git_manager.rollback = MagicMock(return_value=True)
    agent._test_runner.run_tests = MagicMock(return_value=overrides.get("test_result", _make_test_result()))
    agent._verifier.verify = MagicMock(return_value=overrides.get("verification", _make_verification()))


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 1 — Happy path
# ═════════════════════════════════════════════════════════════════════════════


class TestHappyPath:

    def test_verified_status_on_success(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=False)
        _configure_agent_mocks(agent)
        result = agent.remediate(_make_finding())
        assert result.status == RemediationStatus.VERIFIED

    def test_pr_url_in_result(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=False)
        _configure_agent_mocks(agent)
        result = agent.remediate(_make_finding())
        assert result.pr_url == "https://github.com/owner/repo/pull/42"

    def test_succeeded_property_true(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=False)
        _configure_agent_mocks(agent)
        result = agent.remediate(_make_finding())
        assert result.succeeded is True

    def test_attempts_is_one_on_first_try(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=False)
        _configure_agent_mocks(agent)
        result = agent.remediate(_make_finding())
        assert result.attempts == 1

    def test_patch_captured_in_result(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=False)
        _configure_agent_mocks(agent)
        result = agent.remediate(_make_finding())
        assert len(result.patches) == 1
        assert result.patches[0].patch_hash == "abc123"

    def test_dry_run_skips_git(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=True)
        _configure_agent_mocks(agent)
        result = agent.remediate(_make_finding())
        # git_manager.full_workflow should NOT be called in dry_run mode
        agent._git_manager.full_workflow.assert_not_called()
        # Status can still be VERIFIED in dry run (no git doesn't block it)
        assert result.status == RemediationStatus.VERIFIED


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 2 — Manual review routing
# ═════════════════════════════════════════════════════════════════════════════


class TestManualReview:

    def test_do_not_auto_remediate_routes_to_manual(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=True)
        _configure_agent_mocks(agent, automation_level=RemediationAutomationLevel.DO_NOT_AUTO_REMEDIATE)
        result = agent.remediate(_make_finding())
        assert result.status == RemediationStatus.MANUAL_REVIEW

    def test_manual_review_has_notes(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=True)
        _configure_agent_mocks(agent, automation_level=RemediationAutomationLevel.DO_NOT_AUTO_REMEDIATE)
        result = agent.remediate(_make_finding())
        assert result.manual_review_notes  # Not empty

    def test_plan_requires_manual_review(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=True)
        manual_plan = _make_plan()
        manual_plan.requires_manual_review = True
        manual_plan.fix_strategy = "Manual intervention needed for this complex structural change."
        _configure_agent_mocks(agent, plan=manual_plan)
        result = agent.remediate(_make_finding())
        assert result.status == RemediationStatus.MANUAL_REVIEW


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 3 — Retry loop
# ═════════════════════════════════════════════════════════════════════════════


class TestRetryLoop:

    def test_succeeds_on_second_attempt(self, tmp_repo: Path):
        """Validation fails on attempt 1, succeeds on attempt 2."""
        agent = RemediationAgent(tmp_repo, dry_run=True)
        _configure_agent_mocks(agent)

        call_count = 0
        def validation_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_validation(is_valid=False)
            return _make_validation(is_valid=True)

        agent._patch_validator.validate.side_effect = validation_side_effect

        result = agent.remediate(_make_finding())
        assert result.status == RemediationStatus.VERIFIED
        assert result.attempts == 2

    def test_attempt_counter_increments(self, tmp_repo: Path):
        """Verify attempt counter reflects actual retry count."""
        agent = RemediationAgent(tmp_repo, dry_run=True)
        _configure_agent_mocks(agent)
        # Always invalid → exhausts all attempts
        agent._patch_validator.validate.return_value = _make_validation(is_valid=False)
        result = agent.remediate(_make_finding())
        assert result.attempts == 3  # MAX_REMEDIATION_ATTEMPTS


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 4 — All attempts exhausted
# ═════════════════════════════════════════════════════════════════════════════


class TestAllAttemptsExhausted:

    def test_failed_status_after_all_attempts(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=True)
        _configure_agent_mocks(agent)
        agent._patch_validator.validate.return_value = _make_validation(is_valid=False)
        result = agent.remediate(_make_finding())
        assert result.status == RemediationStatus.FAILED

    def test_failure_reason_set(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=True)
        _configure_agent_mocks(agent)
        agent._patch_validator.validate.return_value = _make_validation(is_valid=False)
        result = agent.remediate(_make_finding())
        assert "3" in result.failure_reason  # mentions max attempts

    def test_multiple_validation_results_captured(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=True)
        _configure_agent_mocks(agent)
        agent._patch_validator.validate.return_value = _make_validation(is_valid=False)
        result = agent.remediate(_make_finding())
        assert len(result.validation_results) == 3


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 5 — Tests fail after patching → rollback
# ═════════════════════════════════════════════════════════════════════════════


class TestTestFailureRollback:

    def test_rolled_back_status_on_test_failure(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=False, block_on_test_failure=True)
        _configure_agent_mocks(agent, test_result=_make_test_result(success=False, ran=True))
        result = agent.remediate(_make_finding())
        assert result.status == RemediationStatus.ROLLED_BACK

    def test_rollback_called_on_test_failure(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=False, block_on_test_failure=True)
        _configure_agent_mocks(agent, test_result=_make_test_result(success=False, ran=True))
        agent.remediate(_make_finding())
        agent._git_manager.rollback.assert_called_once()

    def test_no_rollback_if_block_disabled(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=False, block_on_test_failure=False)
        _configure_agent_mocks(agent, test_result=_make_test_result(success=False, ran=True))
        result = agent.remediate(_make_finding())
        agent._git_manager.rollback.assert_not_called()
        # Still passes through to VERIFIED (tests failed but blocking disabled)
        assert result.status == RemediationStatus.VERIFIED


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 6 — No tests detected
# ═════════════════════════════════════════════════════════════════════════════


class TestNoTestsDetected:

    def test_no_tests_still_verified(self, tmp_repo: Path):
        """When no tests are found, agent warns but continues to VERIFIED."""
        agent = RemediationAgent(tmp_repo, dry_run=True, require_tests=False)
        _configure_agent_mocks(agent, test_result=_make_test_result(ran=False, success=False))
        result = agent.remediate(_make_finding())
        assert result.status == RemediationStatus.VERIFIED

    def test_no_tests_ran_is_false(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=True)
        _configure_agent_mocks(agent, test_result=_make_test_result(ran=False, success=False))
        result = agent.remediate(_make_finding())
        assert result.tests_ran is False


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 7 — Exception handling
# ═════════════════════════════════════════════════════════════════════════════


class TestExceptionHandling:

    def test_locate_exception_returns_failed(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=True)
        _configure_agent_mocks(agent)
        agent._locator.locate.side_effect = RuntimeError("Disk read error")
        result = agent.remediate(_make_finding())
        assert result.status == RemediationStatus.FAILED
        assert "Source location failed" in result.failure_reason

    def test_locate_returns_none_gives_failed(self, tmp_repo: Path):
        agent = RemediationAgent(tmp_repo, dry_run=True)
        _configure_agent_mocks(agent, source_location=None)
        agent._locator.locate.return_value = None
        result = agent.remediate(_make_finding())
        assert result.status == RemediationStatus.FAILED

    def test_patch_generator_exception_retries(self, tmp_repo: Path):
        """PatchGenerator exception on attempt 1 → retry on attempt 2 succeeds."""
        agent = RemediationAgent(tmp_repo, dry_run=True)
        _configure_agent_mocks(agent)

        call_count = 0
        def patch_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Mock patch error")
            return _make_patch()

        agent._patch_generator.generate.side_effect = patch_side_effect
        result = agent.remediate(_make_finding())
        assert result.status == RemediationStatus.VERIFIED
        assert result.attempts == 2


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 8 — RemediationAgentResult properties
# ═════════════════════════════════════════════════════════════════════════════


class TestRemediationAgentResult:

    def test_succeeded_true_only_for_verified(self):
        for status in RemediationStatus:
            r = RemediationAgentResult(finding_id="A11Y-001", status=status)
            if status == RemediationStatus.VERIFIED:
                assert r.succeeded is True
            else:
                assert r.succeeded is False

    def test_initial_state(self):
        r = RemediationAgentResult(finding_id="A11Y-XYZ")
        assert r.finding_id == "A11Y-XYZ"
        assert r.status == RemediationStatus.IN_PROGRESS
        assert r.attempts == 0
        assert r.plans == []
        assert r.patches == []
        assert not r.pr_url
        assert not r.succeeded
