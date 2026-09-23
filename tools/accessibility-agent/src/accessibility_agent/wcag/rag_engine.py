"""
WCAG 2.2 RAG Engine (Retrieval-Augmented Generation) — v0.7

Provides three capabilities:

1. SEMANTIC SEARCH
   Given a finding's rule_id, description, or element HTML, retrieves the
   most relevant WCAG 2.2 success criteria using keyword-based BM25-style scoring.
   No external vector DB required — fully in-memory, zero dependencies.

2. CITATION ENRICHMENT
   Attaches verified W3C source URLs and exact criterion text to every finding
   before the AI reasoning prompt is built, ensuring the AI always grounds
   its response in the official spec.

3. CONTRADICTION DETECTION
   After the AI proposes an HTML fix, validates it against the knowledge base
   to detect if the proposed fix would violate a DIFFERENT WCAG criterion.
   Example: AI suggests aria-hidden="true" on a form label (would break 4.1.2).

Usage:
    from accessibility_agent.wcag.rag_engine import RAGEngine

    engine = RAGEngine()
    results = engine.search("missing alt text on image")
    context = engine.build_context_for_finding(finding)
    issues = engine.check_fix_for_contradictions(proposed_html, sc_being_fixed)
"""

from __future__ import annotations

import math
import re
from typing import Any

from accessibility_agent.wcag.knowledge_base import ARIA_PATTERNS, WCAG_22
from accessibility_agent.wcag.sc_relationships import (
    SCRelationship,
    classify_relationship,
    should_surface_contradiction,
    should_warn_contradiction,
)
from accessibility_agent.logging_config import get_logger

log = get_logger(__name__)


# Known dangerous ARIA patterns that commonly violate WCAG when misused
_CONTRADICTION_PATTERNS: list[dict[str, Any]] = [
    {
        "pattern": r'aria-hidden\s*=\s*["\']true["\']',
        "on_selector": r"(label|button|a\[|input|select|textarea|h[1-6]|[^-]role\s*=\s*[\"'](?:button|link|heading))",
        "violates": "4.1.2",
        "message": (
            "aria-hidden='true' is applied to an interactive or semantic element. "
            "This hides it from screen readers entirely, violating Name, Role, Value (4.1.2). "
            "Only use aria-hidden on purely decorative elements."
        ),
    },
    {
        "pattern": r'role\s*=\s*["\']presentation["\']',
        "on_selector": r"(table|th|td|button|a\[|input|h[1-6])",
        "violates": "1.3.1",
        "message": (
            "role='presentation' on a semantic element (table/heading/input) removes all "
            "structural semantics, violating Info and Relationships (1.3.1)."
        ),
    },
    {
        "pattern": r'tabindex\s*=\s*["\'][1-9]',
        "on_selector": None,
        "violates": "2.4.3",
        "message": (
            "Positive tabindex values (tabindex='1', '2', etc.) create an unpredictable tab order "
            "that overrides the natural DOM flow, violating Focus Order (2.4.3). "
            "Use tabindex='0' to add elements to the tab order, or tabindex='-1' for programmatic focus only."
        ),
    },
    {
        "pattern": r'outline\s*:\s*none|outline\s*:\s*0\b',
        "on_selector": None,
        "violates": "2.4.7",
        "message": (
            "outline:none or outline:0 removes the browser's default focus ring. "
            "Unless a custom focus indicator is provided, this violates Focus Visible (2.4.7). "
            "Always pair outline:none with a visible :focus or :focus-visible replacement style."
        ),
    },
    {
        "pattern": r'<div[^>]*onclick|<span[^>]*onclick',
        "on_selector": None,
        "violates": "4.1.2",
        "message": (
            "A <div> or <span> with onclick is used as an interactive control. "
            "Without role='button' and keyboard handlers, this violates Name, Role, Value (4.1.2). "
            "Use <button> instead, or add role='button', tabindex='0', and keyboard event handlers."
        ),
    },
    {
        "pattern": r'alt\s*=\s*["\']["\']',
        "on_selector": r"<img[^>]*(role\s*=\s*[\"'](?:button|link|img)|aria-label)",
        "violates": "1.1.1",
        "message": (
            "An image with a functional role (button/link/img with aria-label) has alt='' (empty). "
            "Empty alt removes the image from the accessibility tree. "
            "Functional images must have a descriptive alt attribute (1.1.1)."
        ),
    },
]


class RAGEngine:
    """
    Retrieval-Augmented Generation engine for WCAG 2.2.

    Provides keyword-based semantic search over the WCAG knowledge base,
    builds rich context strings for AI prompts, and validates proposed fixes
    against the full specification.
    """

    def __init__(self) -> None:
        self._kb = WCAG_22
        self._aria = ARIA_PATTERNS
        # Precompute inverted keyword index for fast lookup
        self._index = self._build_index()
        log.info("rag_engine.initialized", criteria_count=len(self._kb))

    def _build_index(self) -> dict[str, list[str]]:
        """Build keyword → [sc_number, ...] inverted index."""
        index: dict[str, list[str]] = {}
        for sc, data in self._kb.items():
            all_terms = (
                data.get("keywords", [])
                + data.get("common_failures", [])
                + [data["title"].lower()]
                + data.get("axe_rules", [])
            )
            for term in all_terms:
                for word in re.findall(r"\w+", term.lower()):
                    if len(word) > 2:  # Skip very short words
                        if word not in index:
                            index[word] = []
                        if sc not in index[word]:
                            index[word].append(sc)
        return index

    def search(self, query: str, top_n: int = 3) -> list[dict[str, Any]]:
        """
        Search the knowledge base for the most relevant WCAG criteria.

        Uses TF-IDF-inspired keyword scoring:
        - Each query word that matches a criterion's keywords increases its score
        - Inverse document frequency favors specific terms over common ones
        - Returns top_n results sorted by relevance score

        Args:
            query: Natural language query or finding description
            top_n: Maximum number of results to return

        Returns:
            List of dicts with sc, title, level, url, score, and context
        """
        query_words = set(re.findall(r"\w+", query.lower()))
        query_words = {w for w in query_words if len(w) > 2}

        scores: dict[str, float] = {}

        for word in query_words:
            matching_scs = self._index.get(word, [])
            if not matching_scs:
                continue
            # IDF: rarer terms (fewer matching criteria) score higher
            idf = math.log(len(self._kb) / (1 + len(matching_scs)))
            for sc in matching_scs:
                scores[sc] = scores.get(sc, 0.0) + idf

        if not scores:
            return []

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]

        results = []
        for sc, score in ranked:
            data = self._kb[sc]
            results.append({
                "sc": sc,
                "title": data["title"],
                "level": data["level"],
                "principle": data["principle"],
                "url": data["url"],
                "description": data["description"],
                "fix_hint": data.get("fix_hint", ""),
                "common_failures": data.get("common_failures", []),
                "aria_patterns": data.get("aria_patterns", []),
                "score": round(score, 3),
            })

        return results

    def get_criterion(self, sc: str) -> dict[str, Any] | None:
        """Retrieve a specific criterion by its SC number (e.g. '4.1.2')."""
        return self._kb.get(sc)

    def get_aria_pattern(self, role: str) -> dict[str, Any] | None:
        """Retrieve ARIA design pattern guidance for a role (e.g. 'dialog', 'button')."""
        return self._aria.get(role)

    def build_context_for_finding(self, finding_data: dict[str, Any]) -> str:
        """
        Build a rich WCAG context block to inject into an AI reasoning prompt.

        Takes a finding's rule_id, description, and element info, looks up:
        - The primary WCAG criterion (from the finding's wcag.success_criterion)
        - Semantically related criteria (via keyword search)
        - Relevant ARIA pattern if applicable

        Returns a formatted string ready for inclusion in a prompt.
        """
        sc = finding_data.get("wcag", {}).get("success_criterion", "")
        description = finding_data.get("description", "")
        rule_id = finding_data.get("rule_id", "")
        element_html = finding_data.get("element", {}).get("html", "")

        lines: list[str] = ["## WCAG 2.2 Knowledge Base Context\n"]

        # Primary criterion
        primary = self.get_criterion(sc)
        if primary:
            lines.append(f"### Primary Criterion: {sc} — {primary['title']} (Level {primary['level']})")
            lines.append(f"**Source:** {primary['url']}")
            lines.append(f"**Official Description:** {primary['description']}")
            if primary.get("fix_hint"):
                lines.append(f"**Fix Guidance:** {primary['fix_hint']}")
            if primary.get("common_failures"):
                lines.append("**Known Failure Patterns:**")
                for cf in primary["common_failures"][:4]:
                    lines.append(f"  - {cf}")
            lines.append("")

        # Related criteria via semantic search
        query = f"{description} {rule_id} {element_html[:200]}"
        related = [r for r in self.search(query, top_n=5) if r["sc"] != sc][:2]
        if related:
            lines.append("### Related WCAG Criteria to Consider:")
            for r in related:
                lines.append(f"- **{r['sc']} {r['title']}** (Level {r['level']}): {r['description'][:150]}...")
                lines.append(f"  Source: {r['url']}")
            lines.append("")

        # ARIA pattern context
        for role, pattern in self._aria.items():
            if role in (description + rule_id + element_html).lower():
                lines.append(f"### Relevant ARIA Pattern: {pattern['name']}")
                lines.append(f"**Source:** {pattern['url']}")
                if pattern.get("required_attrs"):
                    lines.append(f"**Required Attributes:** {', '.join(pattern['required_attrs'])}")
                if pattern.get("focus_management"):
                    lines.append(f"**Focus Management:** {pattern['focus_management']}")
                if pattern.get("anti_patterns"):
                    lines.append("**Anti-Patterns to Avoid:**")
                    for ap in pattern["anti_patterns"][:3]:
                        lines.append(f"  - {ap}")
                lines.append("")
                break

        lines.append(
            "IMPORTANT: Base your entire analysis on the WCAG 2.2 source URLs above. "
            "Every fix you recommend must be verified against the official specification."
        )

        return "\n".join(lines)

    def check_fix_for_contradictions(
        self, proposed_html: str, sc_being_fixed: str
    ) -> list[dict[str, str]]:
        """
        Validate a proposed HTML fix for contradictions with other WCAG criteria.

        Uses the SC Relationship Map (sc_relationships.py) to classify whether
        a triggered anti-pattern is:
          - ACTUAL_CONFLICT       → returned in the result list (blocking)
          - POTENTIALLY_INTERACTING → returned with is_blocking=False (warning only)
          - RELATED / COMPATIBLE / UNRELATED → silently ignored (not false positives)

        Args:
            proposed_html:  The AI-generated HTML fix to validate
            sc_being_fixed: The SC number being addressed (e.g. '1.1.1')

        Returns:
            List of contradiction dicts. Each dict has:
                - violates_sc:     SC number of the potential violation
                - violates_title:  Human-readable criterion title
                - violates_url:    W3C specification URL
                - message:         Explanation of the problem
                - matched_pattern: The matched text snippet
                - relationship:    SCRelationship value ('actual_conflict' | 'potentially_interacting' | ...)
                - is_blocking:     True only for ACTUAL_CONFLICT

            Empty list means NO detected contradictions of ANY kind.
        """
        if not proposed_html:
            return []

        contradictions: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        html_lower = proposed_html.lower()

        for idx, cp in enumerate(_CONTRADICTION_PATTERNS):
            sc_of_pattern = cp["violates"]

            # ── Classify the relationship ──────────────────────────────────────
            relationship = classify_relationship(
                sc_being_fixed=sc_being_fixed,
                sc_of_pattern=sc_of_pattern,
                pattern_index=idx,
            )

            # COMPATIBLE / RELATED / UNRELATED → skip entirely (not contradictions)
            if relationship in (
                SCRelationship.COMPATIBLE,
                SCRelationship.RELATED,
                SCRelationship.UNRELATED,
            ):
                log.debug(
                    "rag_engine.contradiction_skipped",
                    sc_fixing=sc_being_fixed,
                    sc_pattern=sc_of_pattern,
                    relationship=relationship.value,
                )
                continue

            # ── Check if the pattern actually appears in the HTML ──────────────
            pattern_match = re.search(cp["pattern"], html_lower, re.IGNORECASE)
            if not pattern_match:
                continue

            # ── Check element context if required ─────────────────────────────
            if cp.get("on_selector"):
                context_match = re.search(cp["on_selector"], html_lower, re.IGNORECASE)
                if not context_match:
                    continue

            # ── Build the contradiction record ─────────────────────────────────
            sc_data = self._kb.get(sc_of_pattern, {})
            record = {
                "violates_sc": sc_of_pattern,
                "violates_title": sc_data.get("title", "Unknown"),
                "violates_url": sc_data.get("url", ""),
                "message": cp["message"],
                "matched_pattern": pattern_match.group(0)[:80],
                "relationship": relationship.value,
                "is_blocking": str(should_surface_contradiction(relationship)).lower(),
            }

            if should_surface_contradiction(relationship):
                # ACTUAL_CONFLICT → goes into blocking contradictions
                contradictions.append(record)
                log.warning(
                    "rag_engine.actual_conflict_detected",
                    fixing_sc=sc_being_fixed,
                    violates_sc=sc_of_pattern,
                    relationship=relationship.value,
                )
            elif should_warn_contradiction(relationship):
                # POTENTIALLY_INTERACTING / UNKNOWN → non-blocking warning
                warnings.append(record)
                log.info(
                    "rag_engine.contradiction_warning",
                    fixing_sc=sc_being_fixed,
                    violates_sc=sc_of_pattern,
                    relationship=relationship.value,
                )

        # Return ONLY actual conflicts as the main result.
        # Warnings are included but marked is_blocking=False so callers can
        # present them separately (e.g. in the remediation report but not
        # as patch blockers).
        all_findings = contradictions + warnings

        if all_findings:
            log.info(
                "rag_engine.contradiction_summary",
                fixing_sc=sc_being_fixed,
                blocking=len(contradictions),
                warnings=len(warnings),
                total=len(all_findings),
            )

        return all_findings

    def get_actual_conflicts(
        self, proposed_html: str, sc_being_fixed: str
    ) -> list[dict[str, str]]:
        """
        Convenience method: return only the BLOCKING contradictions.

        The PatchValidator Gate 8 uses this to decide whether to reject a patch.
        Non-blocking warnings are excluded from the gate decision.
        """
        all_findings = self.check_fix_for_contradictions(proposed_html, sc_being_fixed)
        return [f for f in all_findings if f.get("is_blocking") == "true"]

    def format_citation(self, sc: str) -> str:
        """Return a short citation string for embedding in reports."""
        data = self._kb.get(sc)
        if not data:
            return f"WCAG 2.2 § {sc}"
        return f"WCAG 2.2 § {sc} — {data['title']} (Level {data['level']}) — {data['url']}"
