"""
Remediation Data Models — Pydantic v2 schemas for the Remediation Agent.

These models are separate from the Auditor schemas (wcag/schemas.py) to maintain
clean separation of concerns.  The Auditor produces Findings; the Remediation
Agent produces RemediationResults.

All LLM-generated content that flows into these models is validated against
the schema before being stored or acted upon.  No LLM output can bypass
Pydantic validation.

Schema version: 1.0.0
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── Framework Enumeration ─────────────────────────────────────────────────────


class ApplicationFramework(str, Enum):
    """
    The detected front-end / template framework of the target application.

    Detection is based on file system heuristics (package.json contents,
    presence of specific config files, directory structure).
    """

    STATIC_HTML = "static_html"
    REACT = "react"          # .jsx / .tsx with React imports
    NEXT_JS = "next_js"      # React + next.config.js
    VUE = "vue"              # .vue SFC files
    NUXT = "nuxt"            # Vue + nuxt.config.js
    ANGULAR = "angular"      # @angular/core in package.json
    SVELTE = "svelte"        # .svelte files
    DJANGO = "django"        # manage.py + templates/
    FLASK = "flask"          # app.py + templates/ (Jinja2)
    RAILS = "rails"          # .erb templates
    LARAVEL = "laravel"      # .blade.php templates
    UNKNOWN = "unknown"      # Cannot be determined


# ── Source Location ───────────────────────────────────────────────────────────


class SourceMatchConfidence(str, Enum):
    """
    Confidence level of a source location match.

    These levels gate what actions the agent is permitted to take:
    - DIRECT_MATCH / LIKELY_MATCH → may proceed to planning
    - AMBIGUOUS_MATCH             → halts for human disambiguation
    - NOT_FOUND                   → immediately generates manual review package
    """

    DIRECT_MATCH = "direct_match"
    """Exact attribute/class/id found in exactly one file at one location."""

    LIKELY_MATCH = "likely_match"
    """Strong structural evidence in 1–2 files; high confidence but not certain."""

    AMBIGUOUS_MATCH = "ambiguous_match"
    """Found in 3+ files or multiple locations within one file."""

    NOT_FOUND = "not_found"
    """No match found with any strategy."""


class SourceLocation(BaseModel):
    """
    The resolved source-code location responsible for an accessibility finding.

    IMPORTANT: This is the source file on disk, NOT the browser DOM.
    The browser DOM and the source are often structurally different:
    - React JSX compiles to createElement() calls
    - Templates are server-rendered
    - CSS-in-JS generates synthetic class names
    - Bundlers minify and concatenate

    The agent must never assume DOM identity with source.
    """

    file_path: str = Field(
        ...,
        description="Path relative to repo root, e.g. 'src/components/Register.tsx'",
    )
    start_line: int = Field(..., ge=1, description="First line of the relevant code block (1-indexed)")
    end_line: int = Field(..., ge=1, description="Last line of the relevant code block (1-indexed)")
    language: str = Field(
        ...,
        description="File language/type: tsx | jsx | html | vue | py | css | scss | erb | blade",
    )
    confidence: SourceMatchConfidence
    framework: ApplicationFramework = ApplicationFramework.UNKNOWN

    matched_text: str = Field(
        default="",
        description="The exact text snippet that was matched in the source file",
    )
    context_before: str = Field(
        default="",
        description="Up to 15 lines of source code immediately before the match (for understanding)",
    )
    context_after: str = Field(
        default="",
        description="Up to 15 lines of source code immediately after the match (for understanding)",
    )

    search_strategy: str = Field(
        default="",
        description="Which locator strategy produced this result, e.g. 'exact_class_search'",
    )
    match_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Internal ranking score; higher = better match",
    )

    all_candidates: list[str] = Field(
        default_factory=list,
        description="All other file paths that were considered but not selected",
    )

    @field_validator("end_line")
    @classmethod
    def _end_gte_start(cls, v: int, info: Any) -> int:
        start = info.data.get("start_line", 1)
        if v < start:
            raise ValueError(f"end_line ({v}) must be >= start_line ({start})")
        return v

    @property
    def line_count(self) -> int:
        """Number of lines in the identified block."""
        return self.end_line - self.start_line + 1

    @property
    def is_actionable(self) -> bool:
        """True if confidence is sufficient for automated patching."""
        return self.confidence in (
            SourceMatchConfidence.DIRECT_MATCH,
            SourceMatchConfidence.LIKELY_MATCH,
        )


# ── Remediation Automation Level ──────────────────────────────────────────────


class RemediationAutomationLevel(str, Enum):
    """
    Classifies how safe it is for the agent to automatically fix a finding.

    This is determined BEFORE any source code is touched.
    The level gates which remediation policies are permitted.

    SAFE_AUTO_FIX         → Deterministic fix; structural change only; no content judgment.
    LIKELY_AUTO_FIX       → High-confidence fix; mild ambiguity; AI-assisted but clear.
    AI_PROPOSED_FIX       → LLM confident but human PR review recommended.
    MANUAL_REVIEW_REQUIRED→ Semantic/content judgment needed; agent cannot decide.
    DO_NOT_AUTO_REMEDIATE → Risk too high (auth flows, payments, complex interactions).
    """

    SAFE_AUTO_FIX = "safe_auto_fix"
    LIKELY_AUTO_FIX = "likely_auto_fix"
    AI_PROPOSED_FIX = "ai_proposed_fix"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    DO_NOT_AUTO_REMEDIATE = "do_not_auto_remediate"


# ── Patch Models ──────────────────────────────────────────────────────────────


class GeneratedPatch(BaseModel):
    """
    A minimal, machine-validated source code patch in unified diff format.

    IMPORTANT: A patch is only generated after source location is confirmed
    and a remediation plan is approved.  The patch must always be validated
    (PatchValidationResult) before application.
    """

    patch_id: str = Field(
        default_factory=lambda: f"PATCH-{uuid.uuid4().hex[:8].upper()}"
    )
    finding_id: str
    attempt_number: int = Field(default=1, ge=1)

    target_file: str = Field(..., description="Relative path of the file being patched")
    unified_diff: str = Field(
        ...,
        min_length=10,
        description="Complete unified diff in --- a/ +++ b/ format",
    )

    lines_added: int = Field(default=0, ge=0)
    lines_removed: int = Field(default=0, ge=0)
    files_changed: int = Field(default=1, ge=1)

    patch_hash: str = Field(
        default="",
        description="SHA-256 hash of the unified_diff string for integrity verification",
    )
    is_minimal: bool = Field(
        default=False,
        description="True when the patch changes only the lines strictly necessary to fix the issue",
    )
    unrelated_changes_detected: bool = Field(
        default=False,
        description="True if the validator detected changes outside the intended scope",
    )

    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Patch Validation ──────────────────────────────────────────────────────────


class PatchValidationResult(BaseModel):
    """
    Result of the 8-gate patch validation.  ALL gates must pass for is_valid=True.

    Gates:
        1. file_exists           — Target file exists on disk
        2. context_matches       — Expected surrounding context is present in file
        3. applies_cleanly       — git apply --check succeeds
        4. syntax_valid          — File passes linter/parser after patch
        5. no_unrelated_changes  — Only intended lines are modified
        6. no_secrets_detected   — No credentials/tokens introduced
        7. no_invalid_aria       — No obviously incorrect ARIA patterns
        8. no_new_contradictions — No new WCAG SC violations introduced
    """

    is_valid: bool = False

    # Individual gates
    file_exists: bool = False
    context_matches: bool = False
    applies_cleanly: bool = False
    syntax_valid: bool | None = None          # None = check not applicable
    no_unrelated_changes: bool = False
    no_secrets_detected: bool = False
    no_invalid_aria: bool = False
    no_new_contradictions: bool = False

    # Details
    failure_reasons: list[str] = Field(default_factory=list)
    contradiction_details: list[dict[str, str]] = Field(default_factory=list)
    linter_output: str = ""
    validated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Remediation Status ────────────────────────────────────────────────────────


class RemediationStatus(str, Enum):
    """Terminal status of a remediation run."""

    VERIFIED = "verified"
    """The fix was applied, tests passed, and accessibility re-scan confirmed resolution."""

    FAILED = "failed"
    """The fix did not resolve the issue after all retry attempts."""

    ROLLED_BACK = "rolled_back"
    """The fix was applied but caused regressions; the branch was rolled back."""

    MANUAL_REVIEW = "manual_review"
    """The agent could not safely auto-fix; a manual review package was generated."""

    REJECTED = "rejected"
    """The finding was rejected at the input validation or classification stage."""

    IN_PROGRESS = "in_progress"
    """The remediation is currently running."""


# ── Top-Level Remediation Result ──────────────────────────────────────────────


class RemediationResult(BaseModel):
    """
    The complete, immutable result of a remediation run.

    This is the top-level output schema of the Remediation Agent.
    It is validated by Pydantic before storage or reporting.

    SCREEN READER NOTE:
        screen_reader_verified is ALWAYS False.
        The agent does not run NVDA, JAWS, VoiceOver, or any other
        assistive technology.  Actual AT testing requires a human.
    """

    remediation_id: str = Field(
        default_factory=lambda: f"REM-{uuid.uuid4().hex[:8].upper()}"
    )
    finding_id: str
    status: RemediationStatus = RemediationStatus.IN_PROGRESS

    # Classification
    automation_level: RemediationAutomationLevel | None = None
    classification_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # Source
    source: SourceLocation | None = None
    framework_detected: ApplicationFramework = ApplicationFramework.UNKNOWN

    # WCAG mapping (copied from Finding for self-contained audit)
    wcag_criterion: str = ""
    wcag_level: str = ""
    wcag_title: str = ""

    # Fix details
    root_cause: str = ""
    fix_strategy: str = ""
    patch: GeneratedPatch | None = None
    validation: PatchValidationResult | None = None

    # Git
    git_branch: str | None = None
    git_commit: str | None = None
    pr_url: str | None = None
    repo_path: str = ""

    # Test results
    test_results: dict[str, Any] | None = None
    tests_available: bool = False

    # Verification
    before_metrics: dict[str, Any] | None = None
    after_metrics: dict[str, Any] | None = None
    regressions_detected: list[str] = Field(default_factory=list)
    original_issue_resolved: bool = False

    # Rollback
    rollback_performed: bool = False
    rollback_reason: str = ""

    # Manual review
    manual_review_required: bool = False
    manual_review_package: dict[str, Any] | None = None
    manual_review_reason: str = ""

    # Execution
    attempts: int = 0
    max_attempts: int = 3
    audit_trace: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Complete ordered log of every step, tool call, and result",
    )
    errors: list[str] = Field(default_factory=list)

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    # Safety disclaimer
    screen_reader_verified: bool = Field(
        default=False,
        description=(
            "ALWAYS False. The agent does not run assistive technology. "
            "Actual screen reader verification requires human testing with "
            "NVDA, JAWS, VoiceOver, TalkBack, or Narrator."
        ),
    )

    def add_trace_event(
        self,
        step: int,
        action: str,
        result: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append an event to the audit trail."""
        self.audit_trace.append({
            "step": step,
            "action": action,
            "result": result,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def finalize(self, status: RemediationStatus) -> None:
        """Mark the remediation as complete with a terminal status."""
        self.status = status
        self.completed_at = datetime.now(timezone.utc)


# ── Problem Type ──────────────────────────────────────────────────────────────


class ProblemType(str, Enum):
    """
    The category of accessibility problem.

    This drives the patch strategy — different problem types require
    different kinds of fixes (markup vs. ARIA vs. CSS vs. content).
    """

    MISSING_MARKUP = "missing_markup"
    """A required HTML attribute or element is absent (lang, title, alt, label)."""

    INCORRECT_ARIA = "incorrect_aria"
    """An ARIA attribute is wrong, missing, or misused on an element."""

    KEYBOARD_ACCESS = "keyboard_access"
    """An interactive element is not keyboard accessible."""

    FOCUS_VISIBILITY = "focus_visibility"
    """Focus indicator is hidden or removed (outline:none without replacement)."""

    LINK_TEXT = "link_text"
    """Link or button text is non-descriptive ('click here', 'more', 'read more')."""

    IMAGE_ALT = "image_alt"
    """Image is missing an alt attribute or has inappropriate alt text."""

    COLOR_CONTRAST = "color_contrast"
    """Text/background color contrast ratio is below WCAG threshold."""

    SEMANTIC_STRUCTURE = "semantic_structure"
    """Wrong or missing heading hierarchy, landmark, list, or table structure."""

    FORM_LABELING = "form_labeling"
    """Form input is missing an associated label element or aria-label."""

    CONTENT = "content"
    """Issue requires judgment about content meaning or intent."""

    UNKNOWN = "unknown"
    """Could not be classified into a specific problem type."""


# ── Source Context ────────────────────────────────────────────────────────────


class SourceContext(BaseModel):
    """
    Enriched analysis of the source code surrounding a matched location.

    Produced by SourceAnalyzer.  Provides the RemediationPlanner and
    PatchGenerator with everything they need to understand what to change
    and why — without having to re-read the file.
    """

    # Location reference
    file_path: str
    start_line: int
    end_line: int
    language: str
    framework: ApplicationFramework = ApplicationFramework.UNKNOWN

    # The matched element and its context
    matched_element_line: str = Field(
        default="",
        description="The exact source line containing the problematic element",
    )
    block_source: str = Field(
        default="",
        description="Full source block around the match (matched_text + ±15 lines)",
    )

    # Structural analysis
    element_tag: str = Field(
        default="",
        description="HTML tag of the problematic element: button | input | img | a | div | html",
    )
    element_attributes: dict[str, str] = Field(
        default_factory=dict,
        description="Parsed attributes of the problematic element",
    )
    parent_element: str = Field(
        default="",
        description="Immediate parent element description",
    )
    sibling_elements: list[str] = Field(
        default_factory=list,
        description="Nearby sibling elements (for context)",
    )
    nearby_labels: list[str] = Field(
        default_factory=list,
        description="Any <label> elements or aria-label values visible near the match",
    )

    # Framework-specific context
    component_name: str = Field(
        default="",
        description="React/Vue/Angular component name if detectable",
    )
    has_event_handlers: bool = False
    event_handler_names: list[str] = Field(default_factory=list)
    is_inside_form: bool = False
    is_icon_only: bool = Field(
        default=False,
        description="True if element contains only an icon/svg/img with no text",
    )
    is_decorative: bool = Field(
        default=False,
        description="True if element appears to be decorative (no interactive role)",
    )

    # Problem classification
    problem_type: ProblemType = ProblemType.UNKNOWN
    problem_summary: str = Field(
        default="",
        description="One-sentence description of what is wrong",
    )

    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Remediation Plan ──────────────────────────────────────────────────────────


class RemediationPlan(BaseModel):
    """
    A structured, validated plan for fixing a single accessibility finding.

    Produced by RemediationPlanner (AI-assisted but schema-validated).
    The plan is the bridge between diagnosis and the actual code change.

    NOTHING is written to disk until this plan exists and is validated.
    """

    plan_id: str = Field(
        default_factory=lambda: f"PLAN-{uuid.uuid4().hex[:8].upper()}"
    )
    finding_id: str
    attempt_number: int = Field(default=1, ge=1)

    # Classification
    automation_level: RemediationAutomationLevel
    classification_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    classified_by: str = Field(
        default="deterministic_rules",
        description="'deterministic_rules' | 'llm' | 'hybrid'",
    )

    # Problem diagnosis
    problem_type: ProblemType
    root_cause: str = Field(
        ...,
        min_length=10,
        description="Clear, concise explanation of why this is an accessibility violation",
    )

    # Fix strategy
    fix_strategy: str = Field(
        ...,
        min_length=10,
        description="Precise description of what code change will be made",
    )
    expected_change_description: str = Field(
        default="",
        description="Human-readable one-liner of the change, e.g. 'Add aria-label=\"Register\" to button'",
    )
    target_attribute: str = Field(
        default="",
        description="The specific attribute to add/remove/modify, e.g. 'aria-label', 'lang', 'alt'",
    )
    target_value: str = Field(
        default="",
        description="The value to set, if applicable, e.g. 'en', 'Register', ''",
    )

    # Risk
    risk_level: str = Field(
        default="low",
        pattern=r"^(low|medium|high)$",
        description="'low' | 'medium' | 'high'",
    )
    risk_assessment: str = Field(
        default="",
        description="What could go wrong with this fix",
    )

    # WCAG reference
    wcag_criterion: str = ""
    wcag_level: str = ""
    wcag_title: str = ""

    # Testing requirements
    requires_tests: list[str] = Field(
        default_factory=list,
        description="Test files or patterns that should be run after applying the patch",
    )

    # Reasoning trail
    reasoning: str = Field(
        default="",
        description="Full chain-of-thought from the planner (LLM or rules engine)",
    )

    # Flags
    requires_manual_review: bool = False
    manual_review_reason: str = ""

    planned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

