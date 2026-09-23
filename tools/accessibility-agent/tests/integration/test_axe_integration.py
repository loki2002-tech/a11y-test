"""
Integration tests — axe-core engine with Playwright.

These tests serve the corpus pages from a local HTTP server and verify
that the axe engine correctly detects known failures and does not produce
false positives on known-passing pages.

Tests require:
    playwright install chromium
    pip install pytest-playwright pytest-asyncio
"""

from __future__ import annotations

import asyncio
import http.server
import threading
from pathlib import Path
from typing import Generator

import pytest
import pytest_asyncio

from tests.corpus.known_failures import KNOWN_FAILURES_HTML, KNOWN_FAILURES_EXPECTED
from tests.corpus.known_passes import KNOWN_PASSES_HTML, KNOWN_PASSES_EXPECTED_NO_VIOLATIONS


# ── Local HTTP server for serving test fixtures ────────────────────────────────


def _start_server(html: str, port: int) -> http.server.HTTPServer:
    """Start a simple HTTP server serving the given HTML on the specified port."""

    class SinglePageHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        def log_message(self, *args: object) -> None:
            pass  # Silence server logs during tests

    server = http.server.HTTPServer(("127.0.0.1", port), SinglePageHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    return server


@pytest.fixture(scope="module")
def failures_server() -> Generator[str, None, None]:
    server = _start_server(KNOWN_FAILURES_HTML, 18081)
    yield "http://127.0.0.1:18081"
    server.shutdown()


@pytest.fixture(scope="module")
def passes_server() -> Generator[str, None, None]:
    server = _start_server(KNOWN_PASSES_HTML, 18082)
    yield "http://127.0.0.1:18082"
    server.shutdown()


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_axe_detects_known_failures(failures_server: str):
    """
    Evaluation test: axe-core MUST detect every known failure.

    Measures recall: the proportion of known violations detected.
    Target: 100% recall on deterministic/automatable failures.
    """
    from accessibility_agent.accessibility.browser import BrowserController
    from accessibility_agent.accessibility.axe_engine import AxeEngine

    async with BrowserController() as browser:
        await browser.navigate(failures_server)
        engine = AxeEngine(browser)
        result = await engine.run(url=failures_server, page_title="Failures Test")

    confirmed_rule_ids = {
        f.rule_id for f in result.findings
        if f.status.value == "confirmed"
    }

    missed_rules = []
    for expected in KNOWN_FAILURES_EXPECTED:
        rule_id = expected["rule_id"]
        if rule_id not in confirmed_rule_ids:
            missed_rules.append(rule_id)

    assert not missed_rules, (
        f"RECALL FAILURE — axe-core missed these known violations: {missed_rules}\n"
        f"Detected rules: {sorted(confirmed_rule_ids)}"
    )


@pytest.mark.asyncio
async def test_axe_no_false_positives_on_known_passes(passes_server: str):
    """
    Evaluation test: axe-core must NOT produce confirmed violations
    for rules that are correctly implemented.

    Measures false positive rate on rules that should pass.
    """
    from accessibility_agent.accessibility.browser import BrowserController
    from accessibility_agent.accessibility.axe_engine import AxeEngine

    async with BrowserController() as browser:
        await browser.navigate(passes_server)
        engine = AxeEngine(browser)
        result = await engine.run(url=passes_server, page_title="Passes Test")

    false_positives = [
        f for f in result.findings
        if f.status.value == "confirmed"
        and f.rule_id in KNOWN_PASSES_EXPECTED_NO_VIOLATIONS
    ]

    assert not false_positives, (
        f"FALSE POSITIVE FAILURE — These rules should not have fired:\n"
        + "\n".join(
            f"  [{f.rule_id}] {f.description} (selector: {f.element.selector})"
            for f in false_positives
        )
    )


@pytest.mark.asyncio
async def test_findings_conform_to_schema(failures_server: str):
    """
    Schema validation test: every finding produced must be a valid Finding.

    This verifies that the normalization pipeline does not produce
    schema violations that could indicate hallucinated data.
    """
    from accessibility_agent.accessibility.browser import BrowserController
    from accessibility_agent.accessibility.axe_engine import AxeEngine
    from accessibility_agent.wcag.schemas import Finding

    async with BrowserController() as browser:
        await browser.navigate(failures_server)
        engine = AxeEngine(browser)
        result = await engine.run(url=failures_server)

    # Validate each finding by serializing and re-parsing via Pydantic
    from pydantic import ValidationError
    schema_errors = []
    for finding in result.findings:
        try:
            Finding.model_validate(finding.to_dict())
        except ValidationError as exc:
            schema_errors.append(f"{finding.finding_id}: {exc}")

    assert not schema_errors, (
        f"Schema validation failed for {len(schema_errors)} findings:\n"
        + "\n".join(schema_errors)
    )


@pytest.mark.asyncio
async def test_findings_have_wcag_mappings(failures_server: str):
    """
    Every confirmed finding must have a valid, non-empty WCAG mapping.
    """
    from accessibility_agent.accessibility.browser import BrowserController
    from accessibility_agent.accessibility.axe_engine import AxeEngine
    from accessibility_agent.wcag.mapper import wcag_mapper

    async with BrowserController() as browser:
        await browser.navigate(failures_server)
        engine = AxeEngine(browser)
        result = await engine.run(url=failures_server)

    invalid_mappings = []
    for finding in result.findings:
        if finding.status.value == "confirmed":
            if not finding.wcag.success_criterion:
                invalid_mappings.append(finding.finding_id)
            elif not wcag_mapper.validate_mapping(finding.wcag):
                invalid_mappings.append(
                    f"{finding.finding_id} — SC {finding.wcag.success_criterion} invalid"
                )

    assert not invalid_mappings, (
        f"WCAG mapping validation failed: {invalid_mappings}"
    )
