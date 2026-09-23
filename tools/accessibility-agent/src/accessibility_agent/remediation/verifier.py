"""
Verification Engine — compares accessibility scans before and after a patch.

Ensures that the remediation applied actually resolved the issue and did not
introduce new accessibility violations (regressions).
"""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field

from accessibility_agent.logging_config import get_logger
from accessibility_agent.wcag.schemas import Finding

log = get_logger(__name__)


class VerificationStatus(str, Enum):
    """Result of comparing before and after scans."""
    FIXED = "fixed"
    FAILED = "failed"
    REGRESSION = "regression"


class VerificationResult(BaseModel):
    """Output of the VerificationEngine."""
    status: VerificationStatus
    original_finding_id: str
    new_issues_introduced: list[Finding] = Field(default_factory=list)
    message: str = ""

    @property
    def is_successful(self) -> bool:
        """True if the issue was fixed and no regressions were introduced."""
        return self.status == VerificationStatus.FIXED


class VerificationEngine:
    """
    Compares two lists of accessibility findings (before and after a patch)
    to determine if the fix was successful.
    """

    def verify(
        self,
        original_finding: Finding,
        before_scan: list[Finding],
        after_scan: list[Finding],
    ) -> VerificationResult:
        """
        Verify the outcome of a remediation attempt.

        Args:
            original_finding: The specific finding we attempted to fix.
            before_scan: Full list of findings on the page before the fix.
            after_scan: Full list of findings on the page after the fix.
        """
        log.info("verifier.start", finding_id=original_finding.finding_id)

        # 1. Check if the original finding is still present.
        # We consider it present if any finding in the after_scan matches it.
        still_present = any(
            self._is_same_issue(original_finding, f) for f in after_scan
        )

        # 2. Check for NEW issues introduced by the patch.
        # An issue is 'new' if it's in after_scan but not in before_scan.
        new_issues: list[Finding] = []
        for after_f in after_scan:
            # If this after_f is NOT found in before_scan, it's new
            was_in_before = any(self._is_same_issue(after_f, before_f) for before_f in before_scan)
            if not was_in_before:
                new_issues.append(after_f)

        # 3. Determine overall status
        if still_present:
            status = VerificationStatus.FAILED
            msg = f"The issue '{original_finding.rule_id}' is still present after patching."
            log.warning("verifier.failed", finding_id=original_finding.finding_id)
        elif new_issues:
            status = VerificationStatus.REGRESSION
            msg = f"Issue fixed, but introduced {len(new_issues)} new accessibility violation(s)."
            log.warning("verifier.regression", finding_id=original_finding.finding_id, new_issues=len(new_issues))
        else:
            status = VerificationStatus.FIXED
            msg = "Issue fixed successfully with zero regressions."
            log.info("verifier.success", finding_id=original_finding.finding_id)

        return VerificationResult(
            status=status,
            original_finding_id=original_finding.finding_id,
            new_issues_introduced=new_issues,
            message=msg,
        )

    def _is_same_issue(self, f1: Finding, f2: Finding) -> bool:
        """
        Heuristic to determine if two findings represent the same underlying issue.
        Since DOM structure might change slightly due to a fix (e.g. changing a tag),
        exact finding_id match is not always sufficient.
        """
        # 1. Exact match (fast path)
        if f1.finding_id and f1.finding_id == f2.finding_id:
            return True

        # 2. Signature match (axe_engine provides this for logical deduplication)
        if f1.component_signature and f1.component_signature == f2.component_signature:
            return True

        # 3. Fuzzy match (same URL, same rule, and highly similar selector)
        if f1.url == f2.url and f1.rule_id == f2.rule_id:
            s1 = f1.element.selector
            s2 = f2.element.selector
            if s1 and s2:
                # If they are exactly the same selector
                if s1 == s2:
                    return True
                # If one is a tight subset of the other (e.g. div.btn vs button.btn)
                # We do a basic check: if they share the same ID or main classes
                if self._selectors_are_similar(s1, s2):
                    return True

        return False

    @staticmethod
    def _selectors_are_similar(s1: str, s2: str) -> bool:
        """
        Checks if two CSS selectors likely point to the same component
        even if the tag changed.
        Example: 'div#submit' and 'button#submit' -> True
        """
        # Extract IDs (#some-id)
        id1 = [part for part in s1.replace(" ", ">").split(">")[-1].split("#") if part][1:] if "#" in s1 else []
        id2 = [part for part in s2.replace(" ", ">").split(">")[-1].split("#") if part][1:] if "#" in s2 else []
        if id1 and id1 == id2:
            return True

        # Extract Classes (.some-class)
        # Just grab all classes from the last segment of the selector
        seg1 = s1.replace(" ", ">").split(">")[-1]
        seg2 = s2.replace(" ", ">").split(">")[-1]
        
        classes1 = set([part for part in seg1.split(".") if part][1:]) if "." in seg1 else set()
        classes2 = set([part for part in seg2.split(".") if part][1:]) if "." in seg2 else set()
        
        # If they share at least one class and both have classes, consider them similar enough 
        # (given they already matched URL and rule_id)
        if classes1 and classes2 and not classes1.isdisjoint(classes2):
            return True

        return False
