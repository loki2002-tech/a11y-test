"""
Unit tests for WCAG schemas (Pydantic models).

Tests verify:
- Valid findings pass validation
- Confirmed findings without evidence are rejected
- Invalid SC numbers are rejected
- SC format validation works correctly
- Status enum values are correct
- ScanResult metrics computation is accurate
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from accessibility_agent.wcag.schemas import (
    DetectionMethod,
    ElementLocator,
    EvidenceItem,
    Finding,
    FindingStatus,
    ImpactLevel,
    ScanResult,
    WCAGLevel,
    WCAGMapping,
    WCAGPrinciple,
    WCAGVersion,
)


def make_wcag_mapping(**kwargs) -> WCAGMapping:
    defaults = {
        "version": WCAGVersion.V22,
        "success_criterion": "1.1.1",
        "title": "Non-text Content",
        "level": WCAGLevel.A,
        "principle": WCAGPrinciple.PERCEIVABLE,
        "url": "https://www.w3.org/TR/WCAG22/#non-text-content",
    }
    defaults.update(kwargs)
    return WCAGMapping(**defaults)


def make_evidence_item(**kwargs) -> EvidenceItem:
    defaults = {
        "evidence_type": "tool_output",
        "description": "axe-core output",
        "data": '{"rule": "image-alt"}',
    }
    defaults.update(kwargs)
    return EvidenceItem(**defaults)


def make_confirmed_finding(**kwargs) -> Finding:
    """Helper: creates a valid CONFIRMED finding with required evidence."""
    defaults = {
        "url": "https://example.com",
        "status": FindingStatus.CONFIRMED,
        "detection_method": DetectionMethod.AUTOMATED,
        "wcag": make_wcag_mapping(),
        "description": "Image is missing an alt attribute.",
        "evidence": [make_evidence_item()],
    }
    defaults.update(kwargs)
    return Finding(**defaults)


class TestWCAGMapping:
    def test_valid_mapping(self):
        m = make_wcag_mapping()
        assert m.success_criterion == "1.1.1"
        assert m.level == WCAGLevel.A

    def test_invalid_sc_format_raises(self):
        # Pydantic v2 rejects this via the pattern validator before custom validator runs
        with pytest.raises(ValidationError):
            make_wcag_mapping(success_criterion="1.1")

    def test_invalid_sc_letters_raises(self):
        with pytest.raises(ValidationError):
            make_wcag_mapping(success_criterion="A.B.C")

    def test_sc_243_valid(self):
        m = make_wcag_mapping(
            success_criterion="2.4.3",
            title="Focus Order",
            level=WCAGLevel.A,
            principle=WCAGPrinciple.OPERABLE,
        )
        assert m.success_criterion == "2.4.3"

    def test_sc_1412_valid(self):
        m = make_wcag_mapping(
            success_criterion="1.4.12",
            title="Text Spacing",
            level=WCAGLevel.AA,
            principle=WCAGPrinciple.PERCEIVABLE,
        )
        assert m.success_criterion == "1.4.12"


class TestFinding:
    def test_valid_confirmed_finding(self):
        f = make_confirmed_finding()
        assert f.status == FindingStatus.CONFIRMED
        assert len(f.evidence) == 1
        assert f.confidence == 1.0

    def test_confirmed_finding_without_evidence_raises(self):
        """Phase 10: Confirmed findings MUST have evidence."""
        with pytest.raises(ValidationError, match="no evidence"):
            Finding(
                url="https://example.com",
                status=FindingStatus.CONFIRMED,
                detection_method=DetectionMethod.AUTOMATED,
                wcag=make_wcag_mapping(),
                description="Missing alt text",
                evidence=[],  # No evidence — must fail
            )

    def test_requires_manual_review_can_have_no_evidence(self):
        """Manual review findings don't require evidence at creation time."""
        f = Finding(
            url="https://example.com",
            status=FindingStatus.REQUIRES_MANUAL_REVIEW,
            detection_method=DetectionMethod.SEMI_AUTOMATED,
            wcag=make_wcag_mapping(),
            description="Needs manual verification",
            evidence=[],
        )
        assert f.status == FindingStatus.REQUIRES_MANUAL_REVIEW

    def test_finding_id_generated_automatically(self):
        f = make_confirmed_finding()
        assert f.finding_id.startswith("A11Y-")
        assert len(f.finding_id) > 5

    def test_html_truncated_at_2048_chars(self):
        long_html = "x" * 5000
        locator = ElementLocator(html=long_html)
        assert len(locator.html) == 2048

    def test_to_dict_is_json_serialisable(self):
        import json
        f = make_confirmed_finding()
        d = f.to_dict()
        json_str = json.dumps(d)  # Must not raise
        assert "finding_id" in json_str

    def test_confidence_must_be_between_0_and_1(self):
        with pytest.raises(ValidationError):
            make_confirmed_finding(confidence=1.5)
        with pytest.raises(ValidationError):
            make_confirmed_finding(confidence=-0.1)

    def test_url_must_not_be_empty(self):
        with pytest.raises(ValidationError):
            make_confirmed_finding(url="")


class TestScanResult:
    def test_finalize_computes_metrics(self):
        result = ScanResult(url="https://example.com", scan_mode="automated")

        f1 = make_confirmed_finding()
        f1.wcag = make_wcag_mapping(level=WCAGLevel.A)
        result.add_finding(f1)

        f2 = Finding(
            url="https://example.com",
            status=FindingStatus.REQUIRES_MANUAL_REVIEW,
            detection_method=DetectionMethod.SEMI_AUTOMATED,
            wcag=make_wcag_mapping(level=WCAGLevel.AA),
            description="Needs review",
        )
        result.add_finding(f2)

        result.finalize()

        assert result.metrics.confirmed_findings == 1
        assert result.metrics.manual_review_findings == 1
        assert result.metrics.level_a_findings == 1
        assert result.metrics.level_aa_findings == 0  # f2 is REQUIRES_MANUAL_REVIEW, not CONFIRMED

    def test_run_id_assigned_to_findings_on_add(self):
        result = ScanResult(url="https://example.com", scan_mode="automated")
        f = make_confirmed_finding()
        result.add_finding(f)
        assert f.run_id == result.run_id

    def test_duplicate_findings_excluded_from_total(self):
        result = ScanResult(url="https://example.com", scan_mode="automated")

        canonical = make_confirmed_finding()
        duplicate = make_confirmed_finding()
        duplicate.duplicate_of = canonical.finding_id

        result.add_finding(canonical)
        result.add_finding(duplicate)
        result.finalize()

        # Total findings = non-duplicates only
        assert result.metrics.total_findings == 1
        assert result.metrics.duplicate_findings == 1
