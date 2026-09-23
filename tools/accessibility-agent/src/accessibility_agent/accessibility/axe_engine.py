"""
axe-core Integration Engine.

Responsibilities:
1. Download and cache the axe-core script if not already present.
2. Run axe-core against a loaded page via BrowserController.
3. Normalize raw axe results into structured Finding objects.
4. Map axe rule IDs to WCAG Success Criteria using the WCAGMapper.
5. Generate evidence items for each violation.

Design:
    This module is PURELY DETERMINISTIC.  No LLM calls are made here.
    All findings produced here are labelled detection_method=AUTOMATED
    and confidence=1.0 (for violations) or appropriate lower values
    for incomplete/needs-review items.

References:
    axe-core API: https://www.deque.com/axe/core-documentation/api-documentation/
    axe-core rules: https://dequeuniversity.com/rules/axe/
    axe-core version: 4.9.1 (verify at build time)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from accessibility_agent.accessibility.browser import BrowserController
from accessibility_agent.config import settings
from accessibility_agent.logging_config import get_logger
from accessibility_agent.wcag.mapper import wcag_mapper
from accessibility_agent.wcag.schemas import (
    DetectionMethod,
    ElementLocator,
    EvidenceItem,
    Finding,
    FindingStatus,
    ImpactLevel,
    WCAGLevel,
    WCAGMapping,
    WCAGPrinciple,
    WCAGVersion,
)

log = get_logger(__name__)

# axe-core CDN URL — pin to specific version for reproducibility
AXE_CDN_URL = f"https://cdnjs.cloudflare.com/ajax/libs/axe-core/{settings.axe_version}/axe.min.js"
AXE_CACHE_DIR = Path.home() / ".cache" / "accessibility-agent" / "axe"
AXE_SCRIPT_PATH = AXE_CACHE_DIR / f"axe-{settings.axe_version}.min.js"

# axe impact → our ImpactLevel mapping
AXE_IMPACT_MAP: dict[str, ImpactLevel] = {
    "critical": ImpactLevel.CRITICAL,
    "serious": ImpactLevel.SERIOUS,
    "moderate": ImpactLevel.MODERATE,
    "minor": ImpactLevel.MINOR,
}


async def ensure_axe_script() -> Path:
    """
    Ensure the pinned axe-core script is available locally.
    Downloads once and caches.  Validates via file existence check.
    """
    AXE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if AXE_SCRIPT_PATH.exists() and AXE_SCRIPT_PATH.stat().st_size > 10_000:
        log.debug("axe.cache_hit", path=str(AXE_SCRIPT_PATH))
        return AXE_SCRIPT_PATH

    log.info("axe.downloading", url=AXE_CDN_URL)
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        response = await client.get(AXE_CDN_URL)
        response.raise_for_status()
        AXE_SCRIPT_PATH.write_bytes(response.content)

    log.info("axe.downloaded", size=AXE_SCRIPT_PATH.stat().st_size)
    return AXE_SCRIPT_PATH


def _build_component_signature(rule_id: str, selector: str, sc: str) -> str:
    """
    Build a stable component signature for deduplication.
    Hashes rule_id + normalized selector + SC to identify the same
    logical defect across multiple page states.
    """
    normalized = re.sub(r":nth-child\(\d+\)", "", selector)
    raw = f"{rule_id}::{normalized}::{sc}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _extract_selector(node: dict[str, Any]) -> str:
    """
    Extract the best available CSS selector from an axe result node.
    axe-core provides a 'target' array; we join the first path.
    """
    target = node.get("target", [])
    if not target:
        return ""
    # target may be a list of selectors or a list of lists (for iframes)
    first = target[0]
    if isinstance(first, list):
        return " > ".join(str(s) for s in first)
    return str(first)


def _extract_xpath(node: dict[str, Any]) -> str:
    """Extract XPath from axe node if available in 'ancestry'."""
    # axe doesn't always provide xpath; return empty if not present
    return node.get("xpath", [""])[0] if node.get("xpath") else ""


class AxeEngine:
    """
    Runs axe-core and normalises results into Finding objects.

    Usage:
        engine = AxeEngine(browser_controller)
        results = await engine.run(url, page_title)
    """

    def __init__(self, browser: BrowserController) -> None:
        self._browser = browser

    async def run(self, url: str, page_title: str = "") -> "AxeScanResult":
        """
        Execute a full axe-core scan against the currently loaded page.

        Returns an AxeScanResult containing normalized findings and raw data.
        """
        axe_path = await ensure_axe_script()
        raw = await self._browser.inject_and_run_axe(axe_path)

        findings: list[Finding] = []

        # ── Process Violations (confirmed issues) ──────────────────────────
        for violation in raw.get("violations", []):
            for node in violation.get("nodes", []):
                finding = self._normalize_violation(
                    violation=violation,
                    node=node,
                    url=url,
                    page_title=page_title,
                )
                if finding:
                    findings.append(finding)

        # ── Process Incomplete (needs review) ─────────────────────────────
        for incomplete in raw.get("incomplete", []):
            for node in incomplete.get("nodes", []):
                finding = self._normalize_incomplete(
                    incomplete=incomplete,
                    node=node,
                    url=url,
                    page_title=page_title,
                )
                if finding:
                    findings.append(finding)

        return AxeScanResult(
            raw=raw,
            findings=findings,
            violations_count=len(raw.get("violations", [])),
            passes_count=len(raw.get("passes", [])),
            incomplete_count=len(raw.get("incomplete", [])),
            inapplicable_count=len(raw.get("inapplicable", [])),
        )

    def _normalize_violation(
        self,
        violation: dict[str, Any],
        node: dict[str, Any],
        url: str,
        page_title: str,
    ) -> Finding | None:
        """
        Convert a single axe-core violation node into a Finding.

        Status = CONFIRMED (deterministic rule fired with DOM evidence).
        Confidence = 1.0.
        """
        rule_id = violation.get("id", "unknown")
        selector = _extract_selector(node)
        html = node.get("html", "")
        tags = violation.get("tags", [])

        # Build WCAG mapping — try direct rule mapping first, then tags
        wcag_mappings = wcag_mapper.enrich_from_axe_rule(rule_id)
        if not wcag_mappings:
            sc_list = wcag_mapper.extract_sc_from_axe_tags(tags)
            wcag_mappings = [m for sc in sc_list for m in [wcag_mapper.enrich_from_sc(sc)] if m]

        # If no valid mapping found, use a safe fallback
        if not wcag_mappings:
            log.warning("axe.no_wcag_mapping", rule_id=rule_id, tags=tags)
            wcag = WCAGMapping(
                version=WCAGVersion.V22,
                success_criterion="4.1.2",  # Most generic fallback
                title="Name, Role, Value",
                level=WCAGLevel.A,
                principle=WCAGPrinciple.ROBUST,
                url="https://www.w3.org/TR/WCAG22/#name-role-value",
            )
        else:
            wcag = wcag_mappings[0]  # Primary mapping

        # Build evidence
        all_messages = [
            check.get("message", "")
            for check in node.get("any", []) + node.get("all", []) + node.get("none", [])
        ]
        evidence = EvidenceItem(
            evidence_type="tool_output",
            description=f"axe-core rule '{rule_id}' violation",
            data=json.dumps({
                "rule_id": rule_id,
                "description": violation.get("description", ""),
                "help": violation.get("help", ""),
                "helpUrl": violation.get("helpUrl", ""),
                "impact": violation.get("impact"),
                "tags": tags,
                "html": html,
                "selector": selector,
                "failure_summary": node.get("failureSummary", ""),
                "check_messages": all_messages,
            }, indent=2),
            url=url,
        )

        try:
            finding = Finding(
                url=url,
                page_title=page_title,
                element=ElementLocator(
                    selector=selector,
                    xpath=_extract_xpath(node),
                    html=html,
                ),
                status=FindingStatus.CONFIRMED,
                detection_method=DetectionMethod.AUTOMATED,
                confidence=1.0,
                impact=AXE_IMPACT_MAP.get(violation.get("impact", ""), None),
                rule_id=rule_id,
                wcag=wcag,
                description=violation.get("help", violation.get("description", "")),
                actual_result=node.get("failureSummary", ""),
                expected_result=violation.get("description", ""),
                evidence=[evidence],
                component_signature=_build_component_signature(
                    rule_id, selector, wcag.success_criterion
                ),
            )
        except Exception as exc:
            log.error("axe.finding_validation_error", rule_id=rule_id, error=str(exc))
            return None

        return finding

    def _normalize_incomplete(
        self,
        incomplete: dict[str, Any],
        node: dict[str, Any],
        url: str,
        page_title: str,
    ) -> Finding | None:
        """
        Convert an axe-core 'incomplete' (needs review) result to a Finding.

        Status = REQUIRES_MANUAL_REVIEW.
        Confidence = 0.5 (axe cannot auto-determine pass/fail).
        """
        rule_id = incomplete.get("id", "unknown")
        selector = _extract_selector(node)
        html = node.get("html", "")
        tags = incomplete.get("tags", [])

        wcag_mappings = wcag_mapper.enrich_from_axe_rule(rule_id)
        if not wcag_mappings:
            sc_list = wcag_mapper.extract_sc_from_axe_tags(tags)
            wcag_mappings = [m for sc in sc_list for m in [wcag_mapper.enrich_from_sc(sc)] if m]

        if not wcag_mappings:
            return None  # Skip unmappable incomplete items silently

        wcag = wcag_mappings[0]

        evidence = EvidenceItem(
            evidence_type="tool_output",
            description=f"axe-core rule '{rule_id}' — needs review",
            data=json.dumps({
                "rule_id": rule_id,
                "description": incomplete.get("description", ""),
                "help": incomplete.get("help", ""),
                "helpUrl": incomplete.get("helpUrl", ""),
                "html": html,
                "selector": selector,
                "reason": node.get("any", [{}])[0].get("message", "") if node.get("any") else "",
            }, indent=2),
            url=url,
        )

        try:
            finding = Finding(
                url=url,
                page_title=page_title,
                element=ElementLocator(selector=selector, html=html),
                status=FindingStatus.REQUIRES_MANUAL_REVIEW,
                detection_method=DetectionMethod.SEMI_AUTOMATED,
                confidence=0.5,
                rule_id=rule_id,
                wcag=wcag,
                description=incomplete.get("help", incomplete.get("description", "")),
                actual_result="axe-core could not automatically determine pass/fail.",
                expected_result=incomplete.get("description", ""),
                evidence=[evidence],
                manual_review_required=True,
                component_signature=_build_component_signature(
                    rule_id, selector, wcag.success_criterion
                ),
            )
        except Exception as exc:
            log.error("axe.incomplete_validation_error", rule_id=rule_id, error=str(exc))
            return None

        return finding


class AxeScanResult:
    """Container for a single axe-core scan result."""

    def __init__(
        self,
        raw: dict[str, Any],
        findings: list[Finding],
        violations_count: int,
        passes_count: int,
        incomplete_count: int,
        inapplicable_count: int,
    ) -> None:
        self.raw = raw
        self.findings = findings
        self.violations_count = violations_count
        self.passes_count = passes_count
        self.incomplete_count = incomplete_count
        self.inapplicable_count = inapplicable_count
        self.timestamp = datetime.now(timezone.utc)

    def summary(self) -> dict[str, int]:
        return {
            "violations": self.violations_count,
            "passes": self.passes_count,
            "incomplete": self.incomplete_count,
            "inapplicable": self.inapplicable_count,
            "findings_generated": len(self.findings),
        }
