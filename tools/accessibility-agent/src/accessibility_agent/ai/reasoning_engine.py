"""
AI Reasoning Engine — v2 with Batch Processing.

Takes validated Finding objects and enriches them in BATCHES of 10,
drastically reducing the number of API calls and scan time.

IMPORTANT: This module NEVER creates new findings. It only ENRICHES existing ones.
All enriched fields are clearly marked as AI-Assisted in the schema.
"""

from __future__ import annotations

import json
import re
from typing import Any

from accessibility_agent.ai.llm_client import BaseLLMClient, create_llm_client
from accessibility_agent.ai.prompts import SYSTEM_PROMPT, build_batch_reasoning_prompt
from accessibility_agent.config import settings
from accessibility_agent.logging_config import get_logger
from accessibility_agent.wcag.rag_engine import RAGEngine
from accessibility_agent.wcag.schemas import (
    DetectionMethod,
    Finding,
    FindingStatus,
    RemediationGuidance,
)

log = get_logger(__name__)

# Singleton RAG engine — instantiated once, reused across all batches
_rag_engine: RAGEngine | None = None


def _get_rag() -> RAGEngine:
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = RAGEngine()
    return _rag_engine


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """
    Safely extract a JSON array from an LLM response.
    Returns empty list on failure.
    """
    clean = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("```").strip()
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, list):
            return parsed
        # Sometimes the model wraps the list in {"fixes": [...]}
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list):
                    return v
    except json.JSONDecodeError:
        pass

    # Try to find the first [...] block
    match = re.search(r"\[.*\]", clean, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    log.warning("reasoning_engine.json_array_parse_failed", raw=text[:300])
    return []


class ReasoningEngine:
    """
    Enriches findings with AI reasoning using BATCH processing.

    Usage:
        engine = ReasoningEngine()
        await engine.enrich_findings(findings)
    """

    def __init__(self, llm_client: BaseLLMClient | None = None) -> None:
        self._llm = llm_client or create_llm_client()
        self._batch_size = settings.llm_batch_size
        log.info("reasoning_engine.initialized",
                 provider=self._llm.provider_name,
                 batch_size=self._batch_size)

    @property
    def is_enabled(self) -> bool:
        return self._llm.provider_name != "disabled"

    async def enrich_findings(self, findings: list[Finding]) -> list[Finding]:
        """
        Main entry point. Enriches all confirmed and manual-review findings in batches.
        Returns the same list with enriched fields applied in-place.
        """
        if not self.is_enabled:
            log.info("reasoning_engine.skipped", reason="LLM provider is disabled")
            return findings

        active = [f for f in findings if not f.duplicate_of]
        confirmed = [f for f in active if f.status == FindingStatus.CONFIRMED]
        manual = [f for f in active if f.status == FindingStatus.REQUIRES_MANUAL_REVIEW]

        total = len(confirmed) + len(manual)
        log.info("reasoning_engine.starting",
                 confirmed_count=len(confirmed),
                 manual_review_count=len(manual),
                 batch_size=self._batch_size,
                 estimated_batches=max(1, (total + self._batch_size - 1) // self._batch_size))

        # Process confirmed violations in batches
        await self._process_batches(confirmed)

        # Process manual review items — these get a different prompt
        await self._process_batches(manual, is_manual_review=True)

        log.info("reasoning_engine.complete", total_enriched=total)
        return findings

    async def _process_batches(self, findings: list[Finding], is_manual_review: bool = False) -> None:
        """Chunk findings into batches and process each batch with one API call."""
        rag = _get_rag()

        for i in range(0, len(findings), self._batch_size):
            batch = findings[i: i + self._batch_size]
            batch_num = i // self._batch_size + 1
            log.info("reasoning_engine.processing_batch",
                     batch=batch_num,
                     count=len(batch),
                     is_manual_review=is_manual_review)

            # Build base prompt
            prompt = build_batch_reasoning_prompt(batch, is_manual_review=is_manual_review)

            # Inject WCAG RAG context for the first finding in the batch
            # (batch members typically share the same or closely related criteria)
            if batch:
                primary_finding = batch[0]
                rag_context = rag.build_context_for_finding({
                    "wcag": {
                        "success_criterion": primary_finding.wcag.success_criterion,
                    },
                    "description": primary_finding.description,
                    "rule_id": primary_finding.rule_id,
                    "element": {"html": primary_finding.element.html[:300]},
                })
                # Prepend the RAG context to ground the AI in the official spec
                prompt = f"{rag_context}\n\n---\n\n{prompt}"

            response = await self._llm.generate(prompt=prompt, system=SYSTEM_PROMPT)

            if not response or not response.text:
                log.warning("reasoning_engine.batch_no_response", batch=batch_num)
                continue

            results = _extract_json_array(response.text)

            if not results:
                log.warning("reasoning_engine.batch_parse_failed", batch=batch_num)
                continue

            # Apply each result to the corresponding finding (matched by index)
            for idx, data in enumerate(results):
                if idx >= len(batch):
                    break
                finding = batch[idx]
                try:
                    self._apply_result(finding, data, is_manual_review, rag)
                except Exception as exc:
                    log.warning("reasoning_engine.apply_failed",
                                finding_id=finding.finding_id, error=str(exc))

    def _apply_result(
        self,
        finding: Finding,
        data: dict[str, Any],
        is_manual_review: bool,
        rag: RAGEngine | None = None,
    ) -> None:
        """Apply the AI batch result dict to a single Finding in-place."""
        if is_manual_review:
            verdict = data.get("verdict", "CANNOT_DETERMINE")
            confidence = float(data.get("confidence", 0.5))

            if verdict == "LIKELY_VIOLATION":
                finding.status = FindingStatus.LIKELY
                finding.root_cause = data.get("root_cause", "")
                finding.ai_reasoning = data.get("reasoning", "")
                finding.confidence = confidence
                finding.detection_method = DetectionMethod.AI_ASSISTED
                plain = data.get("plain_english", "")
                if plain:
                    finding.description = plain
                html_fix = data.get("html_fix", "")

                # Run contradiction detection on the proposed fix
                contradictions = []
                if rag and html_fix:
                    contradictions = rag.check_fix_for_contradictions(
                        html_fix, finding.wcag.success_criterion
                    )

                warning_list = []
                if contradictions:
                    for c in contradictions:
                        warning_list.append(
                            f"WCAG {c['violates_sc']} {c['violates_title']}: {c['message']}"
                        )

                finding.remediation = RemediationGuidance(
                    summary=data.get("root_cause", ""),
                    html_fix=html_fix,
                    contradiction_warnings=warning_list,
                )

            elif verdict == "LIKELY_FALSE_POSITIVE":
                finding.status = FindingStatus.PASS
                finding.ai_reasoning = (
                    f"[AI] Likely false positive. Reason: {data.get('reasoning', '')}"
                )
                finding.confidence = confidence
                finding.detection_method = DetectionMethod.AI_ASSISTED
            else:
                finding.ai_reasoning = (
                    f"[AI] Could not determine conclusively. {data.get('reasoning', '')}"
                )
        else:
            finding.root_cause = data.get("root_cause", "")
            finding.ai_reasoning = data.get("reasoning_notes", "")
            finding.detection_method = DetectionMethod.AI_ASSISTED

            plain = data.get("plain_english", "")
            if plain:
                finding.description = plain

            html_fix = data.get("html_fix", "")

            # Run contradiction detection on the proposed fix
            contradictions = []
            if rag and html_fix:
                contradictions = rag.check_fix_for_contradictions(
                    html_fix, finding.wcag.success_criterion
                )

            warning_list = []
            if contradictions:
                for c in contradictions:
                    warning_list.append(
                        f"WCAG {c['violates_sc']} {c['violates_title']}: {c['message']}"
                    )

            finding.remediation = RemediationGuidance(
                summary=data.get("root_cause", ""),
                html_fix=html_fix,
                aria_fix=data.get("aria_fix", ""),
                testing_guidance=data.get("testing_guidance", ""),
                contradiction_warnings=warning_list,
            )

            # Attach the official WCAG citation
            if rag:
                citation = rag.format_citation(finding.wcag.success_criterion)
                if citation and finding.ai_reasoning:
                    finding.ai_reasoning = f"[Citation: {citation}]\n\n{finding.ai_reasoning}"

            ai_confidence = data.get("confidence", 1.0)
            finding.confidence = min(float(ai_confidence), finding.confidence)

            log.info("reasoning_engine.enriched_confirmed",
                     finding_id=finding.finding_id,
                     has_fix=bool(finding.remediation.html_fix),
                     contradictions=len(contradictions))
