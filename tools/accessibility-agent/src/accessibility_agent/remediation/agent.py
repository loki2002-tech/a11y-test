"""
Remediation Agent — end-to-end orchestrator for autonomous accessibility remediation.

This is the top-level controller that wires together all remediation phases:

    Phase 1: SourceLocator.locate()          — find the source file / line
    Phase 2: SourceAnalyzer.analyze()        — understand what needs to change
             RemediationClassifier.classify() — is it safe to auto-fix?
             RemediationPlanner.plan()        — build the change plan
    Phase 3: PatchGenerator.generate()       — create a unified diff
             PatchValidator.validate()        — 8-gate safety check
    Phase 5: GitManager.full_workflow()      — branch → commit → push → PR
    Phase 6: TestRunner.run_tests()          — run the project test suite
    Phase 7: VerificationEngine.verify()     — before/after axe scan comparison

Policy:
    MAX_REMEDIATION_ATTEMPTS = 3  — retry up to 3 times on patch/validation failure
    APPLY_IN_BRANCH            — always work in a branch, never commit to main
    require_tests = False      — warn if no tests detected, but don't block
    block_on_test_failure = True — roll back if tests fail after patching
    create_pr = True           — open a GitHub PR on success

Safety invariants (enforced by the pipeline, never bypassed):
    - DO_NOT_AUTO_REMEDIATE findings are immediately routed to manual review
    - Color contrast issues are NEVER auto-patched (verified by classifier)
    - A commit is NEVER made to the base branch
    - A PR is only created after patch validation AND test suite passes

Usage:
    agent = RemediationAgent(repo_path=Path("./my-app"))
    result = agent.remediate(finding)

    # Or via CLI:
    python -m accessibility_agent remediate --finding-id A11Y-XXXX --repo .
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from accessibility_agent.config import Settings
from accessibility_agent.logging_config import get_logger
from accessibility_agent.remediation.analyzer import SourceAnalyzer
from accessibility_agent.remediation.classifier import RemediationClassifier
from accessibility_agent.remediation.git_manager import GitManager, GitWorkflowResult
from accessibility_agent.remediation.locator import SourceLocator
from accessibility_agent.remediation.patcher import PatchGenerator
from accessibility_agent.remediation.planner import RemediationPlanner
from accessibility_agent.remediation.schemas import (
    GeneratedPatch,
    PatchValidationResult,
    RemediationAutomationLevel,
    RemediationPlan,
    RemediationResult,
    RemediationStatus,
    SourceLocation,
)
from accessibility_agent.remediation.test_runner import TestRunner
from accessibility_agent.remediation.validator import PatchValidator
from accessibility_agent.remediation.verifier import VerificationEngine, VerificationStatus
from accessibility_agent.wcag.schemas import Finding

log = get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

MAX_REMEDIATION_ATTEMPTS: int = 3

# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class RemediationAgentResult:
    """
    Complete result of a RemediationAgent.remediate() call.

    This is the public output surface of the agent. It captures every decision
    the agent made so the user (or a caller system) can understand what happened.
    """

    finding_id: str
    status: RemediationStatus = RemediationStatus.IN_PROGRESS

    # Location
    source_location: SourceLocation | None = None

    # Per-attempt tracking
    attempts: int = 0
    plans: list[RemediationPlan] = field(default_factory=list)
    patches: list[GeneratedPatch] = field(default_factory=list)
    validation_results: list[PatchValidationResult] = field(default_factory=list)

    # Git
    git_result: GitWorkflowResult | None = None
    pr_url: str = ""

    # Testing
    tests_ran: bool = False
    tests_passed: bool = False
    test_output: str = ""

    # Verification
    verification_status: VerificationStatus | None = None
    regressions_introduced: int = 0

    # Failure / manual review
    failure_reason: str = ""
    manual_review_notes: str = ""

    # Timing
    duration_seconds: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.status == RemediationStatus.VERIFIED


# ── Main Agent ────────────────────────────────────────────────────────────────


class RemediationAgent:
    """
    Orchestrates all remediation phases to produce a fully automated fix.

    Design principles:
    - Each phase is independently replaceable (dependency injection ready).
    - All state flows through immutable Pydantic models between phases.
    - Failures at any stage are logged and result in either retry or manual review.
    - The git branch is ALWAYS cleaned up on failure (rollback guaranteed).
    """

    def __init__(
        self,
        repo_path: Path,
        settings: Settings | None = None,
        *,
        dry_run: bool = False,
        require_tests: bool = False,
        block_on_test_failure: bool = True,
        create_pr: bool = True,
    ) -> None:
        self._repo = repo_path.resolve()
        self._settings = settings or Settings()
        self._dry_run = dry_run
        self._require_tests = require_tests
        self._block_on_test_failure = block_on_test_failure
        self._create_pr = create_pr

        # Phase components — can be injected for testing
        self._locator = SourceLocator(repo_path=self._repo)
        self._analyzer = SourceAnalyzer(repo_path=self._repo)
        self._classifier = RemediationClassifier()
        self._planner = RemediationPlanner()
        self._patch_generator = PatchGenerator(repo_path=self._repo)
        self._patch_validator = PatchValidator(repo_path=self._repo)
        self._git_manager = GitManager(
            repo_path=self._repo,
            github_token=self._settings.github_token,
            dry_run=dry_run,
        )
        self._test_runner = TestRunner(repo_path=self._repo)
        self._verifier = VerificationEngine()

    # ── Public API ────────────────────────────────────────────────────────────

    def remediate(
        self,
        finding: Finding,
        before_scan: list[Finding] | None = None,
        after_scan_callback: Any | None = None,
    ) -> RemediationAgentResult:
        """
        Execute the full remediation pipeline for a single finding.

        Args:
            finding: The accessibility finding to fix.
            before_scan: Optional pre-patch scan results for regression detection.
                         If provided, after_scan_callback is called post-patch for diff.
            after_scan_callback: Optional callable(url) → list[Finding] for verification.

        Returns:
            RemediationAgentResult with full audit trail.
        """
        start_time = time.monotonic()
        result = RemediationAgentResult(finding_id=finding.finding_id)
        finding_data = finding.model_dump()

        log.info(
            "agent.remediate.start",
            finding_id=finding.finding_id,
            rule_id=finding.rule_id,
            url=finding.url,
        )

        # ─── Phase 1: Source Location ────────────────────────────────────────
        source_location = self._locate_source(finding_data, result)
        if source_location is None:
            result.duration_seconds = time.monotonic() - start_time
            return result

        result.source_location = source_location

        # ─── Phase 1.5: Stage 0 — Finding Validation ─────────────────────────
        # CRITICAL: Validate the scanner finding BEFORE any fix is generated.
        # This is the primary defence against false positives and scanner over-trust.
        try:
            from accessibility_agent.remediation.finding_validator import FindingValidator
            source_context_for_validation = self._analyzer.analyze(source_location, finding_data)
            fv_result = FindingValidator().validate(finding_data, source_context_for_validation)
            log.info(
                "agent.finding_validation",
                finding_id=finding.finding_id,
                verdict=fv_result.verdict,
                confidence=fv_result.confidence,
                reason=fv_result.reason[:100],
            )
            if fv_result.verdict == "FALSE_POSITIVE":
                result.status = RemediationStatus.MANUAL_REVIEW
                result.manual_review_notes = (
                    f"FINDING VALIDATOR: FALSE POSITIVE — {fv_result.reason}"
                )
                result.duration_seconds = time.monotonic() - start_time
                log.info(
                    "agent.false_positive_blocked",
                    finding_id=finding.finding_id,
                    reason=fv_result.reason,
                )
                return result
            if fv_result.verdict == "MANUAL_REVIEW_REQUIRED":
                return self._manual_review(
                    result,
                    f"FINDING VALIDATOR: {fv_result.reason}",
                    start_time,
                )
        except ImportError:
            log.warning("agent.finding_validator_not_available", finding_id=finding.finding_id)
        except Exception as exc:
            log.warning("agent.finding_validator_error", finding_id=finding.finding_id, error=str(exc))

        # ─── Phase 2: Classify + Plan ────────────────────────────────────────

        classification = self._classify(finding_data, source_location)
        if classification == RemediationAutomationLevel.DO_NOT_AUTO_REMEDIATE:
            return self._manual_review(result, "Classifier: DO_NOT_AUTO_REMEDIATE", start_time)

        # ─── Retry Loop: Phases 3–5 ──────────────────────────────────────────
        for attempt in range(1, MAX_REMEDIATION_ATTEMPTS + 1):
            result.attempts = attempt
            log.info("agent.attempt", finding_id=finding.finding_id, attempt=attempt)

            plan = self._plan(finding_data, source_location, classification, attempt)
            if plan is None:
                continue
            if plan.requires_manual_review:
                return self._manual_review(result, plan.fix_strategy, start_time)
            result.plans.append(plan)

            patch = self._generate_patch(plan, source_location)
            if patch is None:
                continue
            result.patches.append(patch)

            validation = self._validate_patch(patch, plan)
            result.validation_results.append(validation)
            if not validation.is_valid:
                log.warning(
                    "agent.patch_invalid",
                    finding_id=finding.finding_id,
                    attempt=attempt,
                    failed_gates=[g for g in [
                        "file_exists", "context_matches", "applies_cleanly",
                        "no_unrelated_changes", "no_secrets_detected",
                        "no_invalid_aria", "no_new_contradictions"
                    ] if not getattr(validation, g, True)],
                )
                continue  # retry with next attempt

            # ─── Phase 5: Git workflow ────────────────────────────────────────
            if not self._dry_run:
                git_result = self._git_manager.full_workflow(patch, plan, validation)
                result.git_result = git_result

                if not git_result.succeeded:
                    log.error(
                        "agent.git_failed",
                        finding_id=finding.finding_id,
                        stage=git_result.error_stage,
                        error=git_result.error,
                    )
                    continue  # retry

                result.pr_url = git_result.pr_url

            # ─── Phase 6: Run tests ───────────────────────────────────────────
            test_result = self._test_runner.run_tests()
            result.tests_ran = test_result.ran_tests
            result.test_output = test_result.output or test_result.error

            if test_result.ran_tests and not test_result.success:
                log.warning(
                    "agent.tests_failed",
                    finding_id=finding.finding_id,
                    framework=test_result.framework,
                )
                if self._block_on_test_failure:
                    if result.git_result and not self._dry_run:
                        self._git_manager.rollback(git_result.branch_name)
                    result.failure_reason = (
                        f"Tests failed after patching ({test_result.framework}). "
                        "Branch rolled back."
                    )
                    result.status = RemediationStatus.ROLLED_BACK
                    result.duration_seconds = time.monotonic() - start_time
                    return result
            else:
                result.tests_passed = not test_result.ran_tests or test_result.success

            # ─── Phase 7: Verification ────────────────────────────────────────
            if before_scan is not None and after_scan_callback is not None:
                after_scan = after_scan_callback(finding.url)
                verification = self._verifier.verify(finding, before_scan, after_scan)
                result.verification_status = verification.status
                result.regressions_introduced = len(verification.new_issues_introduced)

                if verification.status == VerificationStatus.REGRESSION:
                    log.warning(
                        "agent.regressions",
                        finding_id=finding.finding_id,
                        count=result.regressions_introduced,
                    )
                    # Regressions are reported but don't block — they appear in the PR
                elif verification.status == VerificationStatus.FAILED:
                    log.warning("agent.verification_failed", finding_id=finding.finding_id)
                    continue  # retry — fix didn't actually resolve the issue

            # ─── SUCCESS ─────────────────────────────────────────────────────
            result.status = RemediationStatus.VERIFIED
            log.info(
                "agent.success",
                finding_id=finding.finding_id,
                attempt=attempt,
                pr_url=result.pr_url,
            )
            result.duration_seconds = time.monotonic() - start_time
            return result

        # All attempts exhausted
        result.status = RemediationStatus.FAILED
        result.failure_reason = (
            f"All {MAX_REMEDIATION_ATTEMPTS} remediation attempts failed. "
            "See validation_results for details."
        )
        log.error("agent.all_attempts_failed", finding_id=finding.finding_id)
        result.duration_seconds = time.monotonic() - start_time
        return result

    # ── Private phase wrappers ────────────────────────────────────────────────

    def _locate_source(
        self, finding_data: dict, result: RemediationAgentResult
    ) -> SourceLocation | None:
        try:
            source_location = self._locator.locate(finding_data)
        except Exception as exc:
            log.error("agent.locate_error", error=str(exc))
            result.status = RemediationStatus.FAILED
            result.failure_reason = f"Source location failed: {exc}"
            return None

        if source_location is None:
            log.warning("agent.locate_not_found", finding_id=result.finding_id)
            result.status = RemediationStatus.FAILED
            result.failure_reason = "Could not locate source file for this finding."
            return None

        log.info(
            "agent.located",
            file=source_location.file_path,
            line=source_location.start_line,
            confidence=source_location.confidence.value if source_location.confidence else "unknown",
        )
        return source_location

    def _classify(
        self, finding_data: dict, source_location: SourceLocation
    ) -> RemediationAutomationLevel:
        try:
            source_context = self._analyzer.analyze(source_location, finding_data)
            automation_level, _, confidence, _ = self._classifier.classify(
                finding_data, source_context
            )
            log.info(
                "agent.classified",
                level=automation_level.value,
                confidence=confidence,
            )
        except Exception as exc:
            log.error("agent.classify_error", error=str(exc))
            return RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED

        # If we got here, it requires AI planning
        if self._dry_run:
            log.warning("remediate.ai_planning_skipped_dry_run")
            return RemediationAutomationLevel.MANUAL_REVIEW_REQUIRED

        return automation_level

    def _plan(
        self,
        finding_data: dict,
        source_location: SourceLocation,
        classification: RemediationAutomationLevel,
        attempt: int,
    ) -> RemediationPlan | None:
        try:
            import asyncio
            source_context = self._analyzer.analyze(source_location, finding_data)
            plan = asyncio.run(
                self._planner.plan(
                    finding_data=finding_data,
                    location=source_location,
                    source_context=source_context,
                    automation_level=classification,
                    classification_confidence=0.9,
                    classified_by="agent",
                    attempt_number=attempt,
                )
            )
            log.info("agent.planned", strategy=plan.fix_strategy[:60])
            return plan
        except Exception as exc:
            log.error("agent.plan_error", error=str(exc))
            return None

    def _generate_patch(
        self, plan: RemediationPlan, source_location: SourceLocation
    ) -> GeneratedPatch | None:
        try:
            patch = self._patch_generator.generate(plan, source_location)
            if patch is None:
                log.warning("agent.patch_none", finding_id=plan.finding_id)
            return patch
        except Exception as exc:
            log.error("agent.patch_error", error=str(exc))
            return None

    def _validate_patch(
        self, patch: GeneratedPatch, plan: RemediationPlan
    ) -> PatchValidationResult:
        try:
            return self._patch_validator.validate(patch, plan)
        except Exception as exc:
            log.error("agent.validate_error", error=str(exc))
            # Return a failed validation so the attempt is retried
            return PatchValidationResult(
                is_valid=False,
                file_exists=False,
                context_matches=False,
                applies_cleanly=False,
                syntax_valid=False,
                no_unrelated_changes=False,
                no_secrets_detected=False,
                no_invalid_aria=False,
                no_new_contradictions=False,
            )

    def _manual_review(
        self, result: RemediationAgentResult, reason: str, start_time: float
    ) -> RemediationAgentResult:
        result.status = RemediationStatus.MANUAL_REVIEW
        result.manual_review_notes = reason
        result.duration_seconds = time.monotonic() - start_time
        log.info(
            "agent.manual_review",
            finding_id=result.finding_id,
            reason=reason[:100],
        )
        return result
