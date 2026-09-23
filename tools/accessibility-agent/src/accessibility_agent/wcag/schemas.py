"""
WCAG Schema Definitions — Pydantic v2 models.

These models define the strict, validated data structures used throughout
the agent.  All findings MUST conform to these schemas.  The AI layer is
never allowed to return arbitrary structures — every LLM response that
produces a Finding must be validated against these models before storage.

Schema version: 0.1.0 (aligned with WCAG 2.2)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


# ── Enumerations ─────────────────────────────────────────────────────────────


class WCAGVersion(str, Enum):
    V21 = "2.1"
    V22 = "2.2"


class WCAGLevel(str, Enum):
    A = "A"
    AA = "AA"
    AAA = "AAA"


class WCAGPrinciple(str, Enum):
    PERCEIVABLE = "Perceivable"
    OPERABLE = "Operable"
    UNDERSTANDABLE = "Understandable"
    ROBUST = "Robust"


class FindingStatus(str, Enum):
    """
    The confidence classification of a finding.

    - CONFIRMED: Deterministic rule fired with sufficient evidence.
    - LIKELY: Strong evidence but requires contextual validation.
    - POSSIBLE: Partial evidence; investigation recommended.
    - REQUIRES_MANUAL_REVIEW: Cannot be determined programmatically.
    - PASS: Check was performed and no issue was detected.
    - NOT_APPLICABLE: The criterion does not apply to this content.
    - CANNOT_DETERMINE: Insufficient DOM/runtime information.
    """

    CONFIRMED = "confirmed"
    LIKELY = "likely"
    POSSIBLE = "possible"
    REQUIRES_MANUAL_REVIEW = "requires_manual_review"
    PASS = "pass"
    NOT_APPLICABLE = "not_applicable"
    CANNOT_DETERMINE = "cannot_determine"


class DetectionMethod(str, Enum):
    AUTOMATED = "automated"
    SEMI_AUTOMATED = "semi_automated"
    MANUAL = "manual"
    AI_ASSISTED = "ai_assisted"


class ReviewDecision(str, Enum):
    """Human reviewer decision for a finding."""

    AUTO_CONFIRMED = "auto_confirmed"
    AUTO_PASSED = "auto_passed"
    ACCEPTED = "accepted"          # Human confirmed it is a real issue
    REJECTED = "rejected"          # Human determined it is a false positive
    REQUIRES_MORE_EVIDENCE = "requires_more_evidence"
    DEFERRED = "deferred"


class ImpactLevel(str, Enum):
    """
    Impact on users with disabilities.
    NOTE: This is NOT the same as WCAG conformance level.
    See docs/severity_model.md for the severity classification model.
    """

    CRITICAL = "critical"    # Blocks access entirely
    SERIOUS = "serious"      # Causes significant difficulty
    MODERATE = "moderate"    # Causes some difficulty; workaround exists
    MINOR = "minor"          # Minor inconvenience


# ── Sub-models ────────────────────────────────────────────────────────────────


class WCAGMapping(BaseModel):
    """Maps a finding to a specific WCAG Success Criterion."""

    version: WCAGVersion = WCAGVersion.V22
    success_criterion: str = Field(
        ...,
        pattern=r"^\d+\.\d+\.\d+$",
        examples=["1.1.1", "2.4.7", "4.1.2"],
        description="Success Criterion number in x.y.z format",
    )
    title: str = Field(..., min_length=1, description="Official SC title, e.g. 'Non-text Content'")
    level: WCAGLevel
    principle: WCAGPrinciple
    url: str = Field(
        default="",
        description="Link to the authoritative W3C specification for this SC",
    )

    @field_validator("success_criterion")
    @classmethod
    def _validate_sc_format(cls, v: str) -> str:
        parts = v.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError(f"Invalid Success Criterion format: {v!r}")
        return v


class ElementLocator(BaseModel):
    """All available locator strategies for the affected element."""

    selector: str = Field(default="", description="CSS selector")
    xpath: str = Field(default="", description="XPath expression")
    aria_label: str = Field(default="")
    role: str = Field(default="", description="ARIA / accessible role")
    accessible_name: str = Field(default="")
    accessible_description: str = Field(default="")
    bounding_box: dict[str, float] | None = Field(
        default=None, description="x, y, width, height coordinates"
    )
    html: str = Field(default="", description="Outer HTML of the element (truncated to 2048 chars)")
    text_content: str = Field(default="")

    @field_validator("html")
    @classmethod
    def _truncate_html(cls, v: str) -> str:
        return v[:2048] if len(v) > 2048 else v


class EvidenceItem(BaseModel):
    """A single piece of evidence attached to a finding."""

    evidence_id: str = Field(default_factory=lambda: f"EVD-{uuid.uuid4().hex[:8].upper()}")
    evidence_type: str = Field(
        ...,
        description="One of: screenshot, dom_snippet, ax_tree, computed_style, "
                    "tool_output, interaction_log, network_log, console_log",
    )
    description: str = Field(default="")
    data: str = Field(
        default="",
        description="Raw data: base64 image, JSON string, HTML string, etc.",
    )
    file_path: str = Field(
        default="",
        description="Relative path to persisted evidence file on disk",
    )
    screenshot_ref_id: str = Field(
        default="",
        description="If set, this item is a reference to a shared screenshot in ScanResult.shared_screenshots. "
                    "The 'data' field will be empty; use this key to look up the image.",
    )
    url: str = Field(default="", description="Page URL at time of capture")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    browser: str = Field(default="")
    viewport: str = Field(default="", description="e.g. '1280x720'")


class ManualTestProcedure(BaseModel):
    """Structured manual test procedure for findings that cannot be automated."""

    test_id: str = Field(default_factory=lambda: f"MT-{uuid.uuid4().hex[:8].upper()}")
    purpose: str
    wcag_criterion: str
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    expected_result: str = ""
    what_to_observe: str = ""
    evidence_required: str = ""


class RemediationGuidance(BaseModel):
    """Developer-ready remediation guidance for a confirmed or likely finding."""

    summary: str = Field(default="")
    html_fix: str = Field(default="", description="HTML/template code fix")
    aria_fix: str = Field(default="", description="ARIA attribute additions/removals")
    css_fix: str = Field(default="", description="CSS changes required")
    javascript_fix: str = Field(default="", description="JS/focus management changes")
    keyboard_behavior: str = Field(default="", description="Required keyboard interaction behavior")
    testing_guidance: str = Field(
        default="",
        description="How a developer can verify the fix is correct",
    )
    contradiction_warnings: list[str] = Field(
        default_factory=list,
        description="Warnings from the RAG engine if the proposed fix violates other WCAG rules",
    )


class HumanReview(BaseModel):
    """Records a human reviewer's decision on an AI or automated finding."""

    decision: ReviewDecision
    reviewer: str = Field(default="", description="Reviewer identifier (name or ID)")
    reason: str = Field(default="", description="Explanation for the decision")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    original_status: FindingStatus


# ── Core Finding Schema ───────────────────────────────────────────────────────


class Finding(BaseModel):
    """
    The canonical finding schema.

    Every accessibility finding produced by the system — whether by a
    deterministic rule, AI reasoning, or manual inspection — MUST conform
    to this schema.  No finding may be stored or reported without passing
    Pydantic validation.
    """

    finding_id: str = Field(
        default="",
        description="Stable unique finding identifier. Deterministically generated if not provided.",
    )
    run_id: str = Field(default="", description="Identifies the scan run that produced this finding")
    url: str = Field(..., min_length=1, description="Exact URL where the finding was detected")
    page_title: str = Field(default="")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # ── Element ────────────────────────────────────────────────────────────
    element: ElementLocator = Field(default_factory=ElementLocator)

    # ── Classification ────────────────────────────────────────────────────
    status: FindingStatus
    detection_method: DetectionMethod
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score: 1.0=deterministic, <1.0=AI-estimated",
    )
    impact: ImpactLevel | None = Field(
        default=None,
        description="User impact. See docs/severity_model.md. NOT the same as WCAG level.",
    )
    rule_id: str = Field(
        default="",
        description="axe-core rule ID or internal rule ID that produced this finding",
    )
    act_rule_id: str = Field(
        default="",
        description="W3C ACT Rule ID where applicable (e.g. 'b5c3f8')",
    )

    # ── WCAG ───────────────────────────────────────────────────────────────
    wcag: WCAGMapping

    # ── Content ───────────────────────────────────────────────────────────
    description: str = Field(..., min_length=1, description="Human-readable finding description")
    actual_result: str = Field(default="", description="What was observed")
    expected_result: str = Field(default="", description="What WCAG/ARIA requires")
    root_cause: str = Field(default="", description="AI or engineer-identified root cause")
    ai_reasoning: str = Field(
        default="",
        description="AI reasoning text — clearly labelled, not a substitute for evidence",
    )

    # ── Evidence ──────────────────────────────────────────────────────────
    evidence: list[EvidenceItem] = Field(default_factory=list)

    # ── Remediation ────────────────────────────────────────────────────────
    remediation: RemediationGuidance = Field(default_factory=RemediationGuidance)

    # ── Manual Review ──────────────────────────────────────────────────────
    manual_review_required: bool = False
    manual_test_procedure: ManualTestProcedure | None = None

    # ── Human Review ───────────────────────────────────────────────────────
    human_review: HumanReview | None = None

    # ── Deduplication ──────────────────────────────────────────────────────
    duplicate_of: str | None = Field(
        default=None,
        description="finding_id of the canonical finding if this is a duplicate",
    )
    related_findings: list[str] = Field(default_factory=list)
    component_signature: str = Field(
        default="",
        description="Stable hash identifying the logical component for dedup",
    )

    @model_validator(mode="after")
    def _generate_finding_id(self) -> "Finding":
        """Generate a deterministic ID if none is provided."""
        if not self.finding_id:
            import hashlib
            raw = f"{self.url}::{self.rule_id}::{self.element.selector}::{self.wcag.success_criterion}"
            h = hashlib.sha256(raw.encode()).hexdigest()[:8].upper()
            self.finding_id = f"A11Y-{h}"
        return self

    @model_validator(mode="after")
    def _validate_evidence_for_confirmed(self) -> "Finding":
        """Confirmed findings MUST have at least one piece of evidence."""
        if self.status == FindingStatus.CONFIRMED and not self.evidence:
            raise ValueError(
                f"Finding {self.finding_id} has status=CONFIRMED but no evidence. "
                "Every confirmed finding requires at least one EvidenceItem."
            )
        return self

    @model_validator(mode="after")
    def _validate_manual_review_procedure(self) -> "Finding":
        """Findings requiring manual review should include a procedure."""
        if self.manual_review_required and self.manual_test_procedure is None:
            # Not a hard error at schema level but log a warning
            pass
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# ── Scan-level aggregates ─────────────────────────────────────────────────────


class ScanMetrics(BaseModel):
    """Aggregated metrics for a single scan run."""

    total_pages_tested: int = 0
    total_elements_inspected: int = 0
    total_findings: int = 0
    confirmed_findings: int = 0
    likely_findings: int = 0
    possible_findings: int = 0
    manual_review_findings: int = 0
    passed_checks: int = 0
    not_applicable_checks: int = 0
    cannot_determine_checks: int = 0
    level_a_findings: int = 0
    level_aa_findings: int = 0
    level_aaa_findings: int = 0
    automated_findings: int = 0
    semi_automated_findings: int = 0
    ai_assisted_findings: int = 0
    duplicate_findings: int = 0
    wcag_criteria_affected: list[str] = Field(default_factory=list)
    axe_violations: int = 0
    axe_passes: int = 0
    axe_incomplete: int = 0
    axe_inapplicable: int = 0


class ScanResult(BaseModel):
    """Top-level result of a complete scan run."""

    run_id: str = Field(default_factory=lambda: f"RUN-{uuid.uuid4().hex[:12].upper()}")
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    url: str
    scan_mode: str
    browser: str = ""
    viewport: str = ""
    agent_steps: int = 0
    findings: list[Finding] = Field(default_factory=list)
    metrics: ScanMetrics = Field(default_factory=ScanMetrics)
    execution_trace: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Full ordered trace of agent decisions and tool calls",
    )
    errors: list[str] = Field(default_factory=list, description="Non-fatal errors during scan")
    shared_screenshots: dict[str, str] = Field(
        default_factory=dict,
        description="Map of screenshot_ref_id → base64 PNG. Findings reference these by ID "
                    "instead of embedding their own copy, preventing massive file bloat.",
    )
    scan_plan: list[dict[str, Any]] = Field(
        default_factory=list,
        description="AI-generated test plan produced before scanning begins. "
                    "Each item: {step, action, target, reason}",
    )

    def add_finding(self, finding: Finding) -> None:
        finding.run_id = self.run_id
        self.findings.append(finding)

    def finalize(self) -> None:
        """Compute aggregated metrics from the findings list."""
        self.completed_at = datetime.now(timezone.utc)
        m = self.metrics
        m.total_findings = len([f for f in self.findings if f.duplicate_of is None])
        m.confirmed_findings = len([f for f in self.findings if f.status == FindingStatus.CONFIRMED and f.duplicate_of is None])
        m.likely_findings = len([f for f in self.findings if f.status == FindingStatus.LIKELY and f.duplicate_of is None])
        m.possible_findings = len([f for f in self.findings if f.status == FindingStatus.POSSIBLE and f.duplicate_of is None])
        m.manual_review_findings = len(
            [f for f in self.findings if f.status == FindingStatus.REQUIRES_MANUAL_REVIEW and f.duplicate_of is None]
        )
        m.passed_checks = len([f for f in self.findings if f.status == FindingStatus.PASS and f.duplicate_of is None])
        m.not_applicable_checks = len(
            [f for f in self.findings if f.status == FindingStatus.NOT_APPLICABLE and f.duplicate_of is None]
        )
        m.level_a_findings = len(
            [f for f in self.findings if f.wcag.level == WCAGLevel.A and f.status == FindingStatus.CONFIRMED and f.duplicate_of is None]
        )
        m.level_aa_findings = len(
            [f for f in self.findings if f.wcag.level == WCAGLevel.AA and f.status == FindingStatus.CONFIRMED and f.duplicate_of is None]
        )
        m.automated_findings = len(
            [f for f in self.findings if f.detection_method == DetectionMethod.AUTOMATED and f.duplicate_of is None]
        )
        m.ai_assisted_findings = len(
            [f for f in self.findings if f.detection_method == DetectionMethod.AI_ASSISTED]
        )
        m.duplicate_findings = len([f for f in self.findings if f.duplicate_of is not None])
        m.wcag_criteria_affected = list(
            {f.wcag.success_criterion for f in self.findings if f.status == FindingStatus.CONFIRMED}
        )
