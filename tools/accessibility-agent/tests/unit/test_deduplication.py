"""
Unit tests for the Deduplication Engine.
"""

from __future__ import annotations

import pytest

from accessibility_agent.accessibility.deduplication import DeduplicationEngine
from accessibility_agent.wcag.schemas import (
    DetectionMethod,
    EvidenceItem,
    Finding,
    FindingStatus,
    WCAGLevel,
    WCAGMapping,
    WCAGPrinciple,
    WCAGVersion,
)


def make_finding(
    rule_id: str = "image-alt",
    selector: str = "#img-1",
    sc: str = "1.1.1",
    signature: str = "",
) -> Finding:
    evidence = EvidenceItem(evidence_type="tool_output", data="{}", description="test")
    mapping = WCAGMapping(
        version=WCAGVersion.V22,
        success_criterion=sc,
        title="Non-text Content",
        level=WCAGLevel.A,
        principle=WCAGPrinciple.PERCEIVABLE,
    )
    f = Finding(
        url="https://example.com",
        status=FindingStatus.CONFIRMED,
        detection_method=DetectionMethod.AUTOMATED,
        wcag=mapping,
        description="Test finding",
        evidence=[evidence],
        rule_id=rule_id,
        element__selector=selector,
    )
    if signature:
        f.component_signature = signature
    f.element.selector = selector
    return f


class TestDeduplicationEngine:
    def setup_method(self):
        self.dedup = DeduplicationEngine(similarity_threshold=0.85)

    def test_no_duplicates_unchanged(self):
        findings = [
            make_finding("image-alt", "#img-1", "1.1.1"),
            make_finding("button-name", "#btn-1", "4.1.2"),
        ]
        result = self.dedup.deduplicate(findings)
        assert all(f.duplicate_of is None for f in result)

    def test_identical_signature_marks_duplicate(self):
        f1 = make_finding("image-alt", "#img-1", "1.1.1", signature="abc123")
        f2 = make_finding("image-alt", "#img-2", "1.1.1", signature="abc123")

        result = self.dedup.deduplicate([f1, f2])

        assert f1.duplicate_of is None  # canonical
        assert f2.duplicate_of == f1.finding_id
        assert f2.finding_id in f1.related_findings

    def test_identical_selector_and_rule_marks_duplicate(self):
        f1 = make_finding("image-alt", "#img-1", "1.1.1")
        f2 = make_finding("image-alt", "#img-1", "1.1.1")

        result = self.dedup.deduplicate([f1, f2])

        duplicate_count = sum(1 for f in result if f.duplicate_of is not None)
        assert duplicate_count == 1

    def test_different_rules_same_selector_not_merged(self):
        """Different rules on same element are distinct defects."""
        f1 = make_finding("image-alt",   "#img-1", "1.1.1")
        f2 = make_finding("color-contrast", "#img-1", "1.4.3")

        result = self.dedup.deduplicate([f1, f2])
        assert all(f.duplicate_of is None for f in result)

    def test_pass_findings_not_deduplicated(self):
        """PASS findings should not be subject to deduplication logic."""
        from accessibility_agent.wcag.schemas import EvidenceItem
        evidence = EvidenceItem(evidence_type="tool_output", data="{}", description="pass")
        mapping = WCAGMapping(
            version=WCAGVersion.V22, success_criterion="1.1.1",
            title="Non-text Content", level=WCAGLevel.A,
            principle=WCAGPrinciple.PERCEIVABLE,
        )
        pass_finding = Finding(
            url="https://example.com",
            status=FindingStatus.PASS,
            detection_method=DetectionMethod.AUTOMATED,
            wcag=mapping,
            description="Passes check",
        )
        pass_finding.element.selector = "#img-1"
        pass_finding.rule_id = "image-alt"
        pass_finding.component_signature = "abc123"

        confirmed = make_finding("image-alt", "#img-1", "1.1.1", signature="abc123")

        result = self.dedup.deduplicate([pass_finding, confirmed])
        # Pass finding should never be marked as duplicate
        assert pass_finding.duplicate_of is None
