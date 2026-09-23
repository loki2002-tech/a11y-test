"""
Remediation sub-package.

This package contains the Autonomous Accessibility Remediation Agent —
a closed-loop agent that takes a validated Finding, locates the responsible
source code, generates a minimal patch, validates it, applies it in an
isolated Git branch, runs tests, re-scans accessibility, and proves the
fix worked before creating a Pull Request.

Sub-modules (in execution order):
    schemas     — Pydantic data models (SourceLocation, RemediationResult etc.)
    locator     — SourceLocator: maps DOM element → source file + line numbers
    analyzer    — SourceAnalyzer: reads context, understands component structure
    classifier  — RemediationClassifier: determines automation safety level
    planner     — RemediationPlanner: LLM-assisted fix plan generation
    patcher     — PatchGenerator: produces minimal unified diffs
    validator   — PatchValidator: 8-gate patch validation
    git_manager — GitManager: branch / apply / rollback / commit / PR
    test_runner — TestExecutor: pytest / jest / playwright runner
    verifier    — VerificationEngine: before/after accessibility comparison
    state       — RemediationStateMachine: enforces valid state transitions
    policy      — RemediationPolicy: configurable thresholds and limits
    tools       — Controlled tool wrappers (allowlisted, logged, sandboxed)
    agent       — RemediationAgent: main entry point driving the state machine
"""

from __future__ import annotations

__all__ = [
    "schemas",
    "locator",
]
