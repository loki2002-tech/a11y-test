"""
Unit tests for Phase 7: VerificationEngine (verifier.py)

Groups:
1. is_same_issue logic (ID, signature, and fuzzy selector matching)
2. verify() method (Fixed, Failed, Regression)
"""

from __future__ import annotations

import pytest

from accessibility_agent.wcag.schemas import Finding, ElementLocator, WCAGMapping, WCAGVersion, WCAGLevel, EvidenceItem
from accessibility_agent.remediation.verifier import (
    VerificationEngine,
    VerificationStatus,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_finding(
    finding_id: str = "A11Y-001",
    rule_id: str = None,
    selector: str = None,
    signature: str = None,
    url: str = "http://localhost/"
) -> Finding:
    rule_id = rule_id or f"rule-{finding_id}"
    selector = selector or f"div#{finding_id}"
    signature = signature or f"sig-{finding_id}"
    
    wcag = WCAGMapping(version=WCAGVersion.V22, success_criterion="4.1.2", level=WCAGLevel.A, title="Name, Role, Value", principle="Robust")
    evidence = EvidenceItem(evidence_type="tool_output", data="[]", description="Mock")
    return Finding(
        finding_id=finding_id,
        url=url,
        rule_id=rule_id,
        component_signature=signature,
        element=ElementLocator(selector=selector),
        wcag=wcag,
        status="confirmed",
        detection_method="automated",
        description="Axe-core violation",
        evidence=[evidence],
    )

# ═════════════════════════════════════════════════════════════════════════════
# GROUP 1 — is_same_issue Matching
# ═════════════════════════════════════════════════════════════════════════════

class TestIsSameIssue:
    
    def test_match_by_finding_id(self):
        verifier = VerificationEngine()
        f1 = _make_finding(finding_id="F-1", signature="A", rule_id="color")
        f2 = _make_finding(finding_id="F-1", signature="B", rule_id="focus")
        # Same ID overrides everything else
        assert verifier._is_same_issue(f1, f2) is True

    def test_match_by_component_signature(self):
        verifier = VerificationEngine()
        f1 = _make_finding(finding_id="F-1", signature="SIG-X")
        f2 = _make_finding(finding_id="F-2", signature="SIG-X")
        assert verifier._is_same_issue(f1, f2) is True

    def test_match_by_exact_selector_and_rule(self):
        verifier = VerificationEngine()
        f1 = _make_finding(finding_id="F-1", signature="A", rule_id="image-alt", selector="img.hero")
        f2 = _make_finding(finding_id="F-2", signature="B", rule_id="image-alt", selector="img.hero")
        assert verifier._is_same_issue(f1, f2) is True

    def test_no_match_if_rule_differs(self):
        verifier = VerificationEngine()
        f1 = _make_finding(finding_id="F-1", signature="A", rule_id="image-alt", selector="img.hero")
        f2 = _make_finding(finding_id="F-2", signature="B", rule_id="color-contrast", selector="img.hero")
        assert verifier._is_same_issue(f1, f2) is False

    def test_fuzzy_match_by_id(self):
        """Selector fuzzy match: same rule_id, different HTML tag but same element #id."""
        verifier = VerificationEngine()
        # Must share rule_id for the fuzzy path to trigger
        f1 = _make_finding(finding_id="F-1", rule_id="button-name", signature="A", selector="div#submit-btn")
        f2 = _make_finding(finding_id="F-2", rule_id="button-name", signature="B", selector="button#submit-btn")
        assert verifier._is_same_issue(f1, f2) is True

    def test_fuzzy_match_by_shared_class(self):
        """Selector fuzzy match: same rule_id, different tag but shared CSS class."""
        verifier = VerificationEngine()
        f1 = _make_finding(finding_id="F-1", rule_id="button-name", signature="A", selector="div.btn.primary")
        f2 = _make_finding(finding_id="F-2", rule_id="button-name", signature="B", selector="button.btn.secondary")
        assert verifier._is_same_issue(f1, f2) is True

    def test_fuzzy_match_fails_different_class(self):
        """Selector fuzzy match: same rule_id but no class overlap → different elements."""
        verifier = VerificationEngine()
        f1 = _make_finding(finding_id="F-1", rule_id="button-name", signature="A", selector="div.container")
        f2 = _make_finding(finding_id="F-2", rule_id="button-name", signature="B", selector="button.btn")
        assert verifier._is_same_issue(f1, f2) is False


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 2 — verify() Outcomes
# ═════════════════════════════════════════════════════════════════════════════

class TestVerifyOutcomes:

    def test_fixed_success(self):
        verifier = VerificationEngine()
        original = _make_finding(finding_id="TARGET")
        before_scan = [original, _make_finding(finding_id="OTHER-1")]
        after_scan = [_make_finding(finding_id="OTHER-1")] # TARGET is gone
        
        result = verifier.verify(original, before_scan, after_scan)
        
        assert result.status == VerificationStatus.FIXED
        assert result.is_successful is True
        assert len(result.new_issues_introduced) == 0

    def test_failed_still_present(self):
        verifier = VerificationEngine()
        original = _make_finding(finding_id="TARGET")
        before_scan = [original]
        after_scan = [original] # TARGET still there
        
        result = verifier.verify(original, before_scan, after_scan)
        
        assert result.status == VerificationStatus.FAILED
        assert result.is_successful is False
        assert len(result.new_issues_introduced) == 0

    def test_failed_present_with_different_id_but_same_signature(self):
        verifier = VerificationEngine()
        original = _make_finding(finding_id="TARGET", signature="SIG-X")
        before_scan = [original]
        # Same signature, meaning it's logically the same issue
        after_finding = _make_finding(finding_id="NEW-ID", signature="SIG-X")
        after_scan = [after_finding]
        
        result = verifier.verify(original, before_scan, after_scan)
        
        assert result.status == VerificationStatus.FAILED
        assert result.is_successful is False

    def test_regression_new_issue_introduced(self):
        verifier = VerificationEngine()
        original = _make_finding(finding_id="TARGET")
        before_scan = [original]
        # TARGET is gone, but a new unrelated finding appeared
        new_issue = _make_finding(finding_id="NEW-BUG", signature="SIG-NEW")
        after_scan = [new_issue]
        
        result = verifier.verify(original, before_scan, after_scan)
        
        assert result.status == VerificationStatus.REGRESSION
        assert result.is_successful is False
        assert len(result.new_issues_introduced) == 1
        assert result.new_issues_introduced[0].finding_id == "NEW-BUG"

    def test_regression_even_if_original_failed(self):
        """If original failed AND there are new regressions, we can flag regression."""
        verifier = VerificationEngine()
        original = _make_finding(finding_id="TARGET")
        before_scan = [original]
        new_issue = _make_finding(finding_id="NEW-BUG", signature="SIG-NEW")
        after_scan = [original, new_issue] # TARGET still there AND new issue
        
        result = verifier.verify(original, before_scan, after_scan)
        
        # It's marked as FAILED if original is present. 
        # (Could be debated if it should be REGRESSION, but FAILED takes priority in the current code).
        # Let's verify the current implementation's behavior:
        assert result.status == VerificationStatus.FAILED
        # But we DO record the regressions in the array!
        assert len(result.new_issues_introduced) == 1
        assert result.new_issues_introduced[0].finding_id == "NEW-BUG"
