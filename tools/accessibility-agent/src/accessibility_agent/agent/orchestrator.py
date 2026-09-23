"""
Scan Orchestrator — Version 0.1 (Automated Mode).

Coordinates the full scan pipeline for MODE 1: Automated Accessibility Scan.

Pipeline:
    1. Launch browser (BrowserController)
    2. Navigate to URL
    3. Capture initial evidence (screenshot, DOM, AX tree)
    4. Run axe-core deterministic scan (AxeEngine)
    5. Run additional deterministic checks (DOM analyzer)
    6. Deduplicate findings
    7. Finalize ScanResult
    8. Generate reports

This orchestrator is SYNCHRONOUS in its decision logic — it does not use an
AI agent loop yet (that is Version 0.4+).  It is designed to be the stable
core on which the AI layer is added without modifying this module.

Version: 0.1 — Deterministic only (no LLM calls)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from accessibility_agent.accessibility.axe_engine import AxeEngine
from accessibility_agent.accessibility.browser import BrowserController
from accessibility_agent.accessibility.deduplication import DeduplicationEngine
from accessibility_agent.config import settings
from accessibility_agent.evidence.collector import EvidenceCollector
from accessibility_agent.logging_config import get_logger
from accessibility_agent.reporting.generator import ReportGenerator
from accessibility_agent.wcag.schemas import ScanResult

log = get_logger(__name__)


class ScanOrchestrator:
    """
    Orchestrates a complete accessibility scan run.

    Usage:
        orchestrator = ScanOrchestrator(url="https://example.com", mode="automated")
        result = await orchestrator.run()
    """

    def __init__(
        self,
        url: str,
        mode: str = "automated",
        output_dir: Path | None = None,
        browser_type: str | None = None,
        headless: bool | None = None,
        viewport: tuple[int, int] | None = None,
        report_formats: list[str] | None = None,
        agentic: bool = False,
        run_id: str | None = None,
        # ── Authentication ────────────────────────────────────────────────
        auth_state_path: Path | None = None,
        login_url: str | None = None,
        login_username: str | None = None,
        login_password: str | None = None,
        login_success_url_contains: str | None = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.url = url
        self.mode = mode
        self.agentic = agentic
        self._output_dir = output_dir or settings.report_dir
        self._report_formats = report_formats or settings.report_formats
        self._run_id = run_id

        # Auth
        self._auth_state_path = auth_state_path
        self._login_url = login_url
        self._login_username = login_username
        self._login_password = login_password
        self._login_success_url_contains = login_success_url_contains
        self._progress_callback = progress_callback

        # Browser configuration overrides
        from accessibility_agent.config import BrowserType
        self._browser_type = BrowserType(browser_type) if browser_type else None
        self._headless = headless
        self._viewport_width = viewport[0] if viewport else None
        self._viewport_height = viewport[1] if viewport else None
        self._agentic = agentic

    async def run(self) -> ScanResult:
        """
        Execute the full scan and return a validated ScanResult.

        The scan result is also persisted to disk as configured reports.
        """
        log.info("orchestrator.starting", url=self.url, mode=self.mode)
        settings.ensure_directories()

        result_kwargs = {"url": self.url, "scan_mode": self.mode}
        if hasattr(self, "_run_id") and self._run_id:
            result_kwargs["run_id"] = self._run_id
            
        result = ScanResult(**result_kwargs)
        step = 0

        async with BrowserController(
            browser_type=self._browser_type,
            headless=self._headless,
            viewport_width=self._viewport_width,
            viewport_height=self._viewport_height,
        ) as browser:

            result.browser = browser.browser_type_name
            result.viewport = browser.viewport_string

            evidence_collector = EvidenceCollector(browser, result.run_id)
            axe_engine = AxeEngine(browser)
            dedup = DeduplicationEngine(settings.dedup_similarity_threshold)

            # ── Step 0: Authentication (optional) ─────────────────────────
            if self._auth_state_path is not None:
                log.info("orchestrator.auth.loading_state", path=str(self._auth_state_path))
                try:
                    await browser.load_auth_state(self._auth_state_path)
                    self._trace(result, 0, "load_auth_state", {
                        "path": str(self._auth_state_path)
                    }, {"status": "loaded"})
                except Exception as exc:
                    error_msg = f"Auth state load failed: {exc}"
                    log.error("orchestrator.auth.state_load_failed", error=error_msg)
                    result.errors.append(error_msg)
                    result.finalize()
                    return result

            elif self._login_url is not None:
                if not self._login_username or not self._login_password:
                    error_msg = (
                        "login_url provided but login_username / login_password are missing. "
                        "Pass --login-username and --login-password (or set A11Y_LOGIN_PASSWORD env var)."
                    )
                    log.error("orchestrator.auth.missing_credentials")
                    result.errors.append(error_msg)
                    result.finalize()
                    return result

                log.info("orchestrator.auth.performing_login", login_url=self._login_url)
                login_ok = await browser.perform_login(
                    login_url=self._login_url,
                    username=self._login_username,
                    password=self._login_password,
                    success_url_contains=self._login_success_url_contains,
                )
                self._trace(result, 0, "perform_login", {
                    "login_url": self._login_url,
                    "username": self._login_username,
                }, {"success": login_ok})

                if not login_ok:
                    error_msg = (
                        f"Automated login to {self._login_url} failed. "
                        "Scan aborted to prevent scanning the login page instead of the target."
                    )
                    log.error("orchestrator.auth.login_failed", login_url=self._login_url)
                    result.errors.append(error_msg)
                    result.finalize()
                    return result

            # ── Step 1: Navigate ───────────────────────────────────────────
            step += 1
            log.info("orchestrator.step", step=step, action="navigate", url=self.url)
            try:
                nav_info = await browser.navigate(self.url)
                page_title = nav_info.get("title", "")
                self._trace(result, step, "navigate", {"url": self.url}, nav_info)
            except Exception as exc:
                error_msg = f"Navigation failed: {exc}"
                log.error("orchestrator.navigate_failed", error=error_msg)
                result.errors.append(error_msg)
                result.finalize()
                return result

            # ── Step 2: Initial Evidence Capture ────────────────────────────
            step += 1
            log.info("orchestrator.step", step=step, action="capture_initial_evidence")

            initial_screenshot = await evidence_collector.capture_screenshot(
                label="initial_state",
                full_page=True,
            )
            dom_snapshot = await evidence_collector.capture_dom_snapshot(label="initial_state")
            ax_tree_snapshot = await evidence_collector.capture_ax_tree(label="initial_state")

            self._trace(result, step, "capture_evidence", {}, {
                "screenshot": initial_screenshot.file_path,
                "dom": dom_snapshot.file_path,
                "ax_tree": ax_tree_snapshot.file_path,
            })

            async def _run_scanners(current_screenshot, suffix=""):
                """Helper to run deterministic scanners on current state."""
                # axe-core
                try:
                    axe_result = await axe_engine.run(url=self.url, page_title=page_title)
                    for finding in axe_result.findings:
                        finding.evidence.append(current_screenshot)
                        if finding.element.selector:
                            try:
                                # Fetch the bounding box coordinates for the Locate feature
                                bbox = await browser.get_bounding_box(finding.element.selector)
                                if bbox:
                                    finding.element.bounding_box = bbox
                            except Exception as e:
                                log.warning("orchestrator.bbox_failed", selector=finding.element.selector, error=str(e))
                        result.add_finding(finding)

                    result.metrics.axe_violations += axe_result.violations_count
                    result.metrics.axe_passes += axe_result.passes_count
                    result.metrics.axe_incomplete += axe_result.incomplete_count
                    result.metrics.axe_inapplicable += axe_result.inapplicable_count
                except Exception as exc:
                    log.error("orchestrator.axe_failed", error=str(exc))

                # keyboard
                try:
                    from accessibility_agent.accessibility.keyboard_tester import KeyboardTester
                    kb_tester = KeyboardTester(browser)
                    kb_findings = await kb_tester.run(url=self.url, page_title=page_title)
                    for finding in kb_findings:
                        finding.evidence.append(current_screenshot)
                        if finding.element.selector:
                            try:
                                bbox = await browser.get_bounding_box(finding.element.selector)
                                if bbox:
                                    finding.element.bounding_box = bbox
                            except Exception as e:
                                pass
                        result.add_finding(finding)
                except Exception as exc:
                    log.error("orchestrator.keyboard_failed", error=str(exc))

            # ── Step 3 & 4: Initial Scans ──────────────────────────────────
            step += 1
            log.info("orchestrator.step", step=step, action="run_initial_scans")
            await _run_scanners(initial_screenshot)

            # ── Step 4.5: Form Intelligence Testing ─────────────────────────
            step += 1
            log.info("orchestrator.step", step=step, action="form_testing")
            try:
                from accessibility_agent.accessibility.form_tester import FormTester
                form_tester = FormTester(browser, axe_engine, evidence_collector)
                form_findings = await form_tester.run(url=self.url, page_title=page_title)
                for finding in form_findings:
                    if finding.element.selector:
                        try:
                            bbox = await browser.get_bounding_box(finding.element.selector)
                            if bbox:
                                finding.element.bounding_box = bbox
                        except Exception:
                            pass
                    result.add_finding(finding)
                log.info("orchestrator.form_testing_complete", new_findings=len(form_findings))
                self._trace(result, step, "form_testing", {}, {"new_findings": len(form_findings)})
            except Exception as exc:
                log.error("orchestrator.form_testing_failed", error=str(exc))

            # ── Step 5: Agentic Planning Loop ────────────────────────────────
            if getattr(self, "_agentic", False):
                log.info("orchestrator.agentic_loop_starting")
                from accessibility_agent.agent.planner import AgentPlanner
                planner = AgentPlanner()

                # Get the live accessibility tree for smart planning
                ax_snapshot = await browser.get_accessibility_snapshot()
                page_html = await browser.get_page_source()

                # 5a. Generate the Test Plan BEFORE any interactions
                if planner.is_enabled:
                    step += 1
                    log.info("orchestrator.step", step=step, action="generate_test_plan")
                    test_plan = await planner.generate_test_plan(ax_snapshot, page_html)
                    result.scan_plan = test_plan
                    self._trace(result, step, "generate_test_plan", {}, {"plan_steps": len(test_plan)})

                # 5b. Execute the Observe → Plan → Act loop
                interactions = 0
                max_interactions = 5
                clicked_selectors: set[str] = set()

                while interactions < max_interactions:
                    step += 1
                    # Re-fetch fresh tree and HTML after each interaction
                    current_ax = await browser.get_accessibility_snapshot()
                    current_html = await browser.get_page_source()

                    selectors_to_click = await planner.get_next_interactions(
                        current_ax, current_html, clicked_selectors
                    )

                    if not selectors_to_click:
                        log.info("orchestrator.agentic_loop_no_actions")
                        break

                    target = selectors_to_click[0]
                    log.info("orchestrator.agentic_loop_clicking", target=target)
                    try:
                        await browser.click_element(target)
                    except Exception as click_exc:
                        log.warning("orchestrator.agentic_click_failed", target=target, error=str(click_exc))
                        clicked_selectors.add(target)  # Mark as tried to avoid retry loop
                        interactions += 1
                        continue

                    clicked_selectors.add(target)
                    interactions += 1

                    state_screenshot = await evidence_collector.capture_screenshot(
                        label=f"state_after_click_{interactions}",
                        full_page=True,
                    )

                    # ── Modal Focus Trap Test (v0.6.1) ────────────────────────
                    # After every click, check if a dialog opened.
                    # If yes, run the full keyboard trap test suite immediately.
                    try:
                        from accessibility_agent.accessibility.keyboard_tester import ModalFocusTrapTester
                        modal_tester = ModalFocusTrapTester(browser)
                        dialog_info = await modal_tester.detect_open_dialog()
                        if dialog_info:
                            log.info(
                                "orchestrator.modal_detected",
                                selector=dialog_info.get("selector"),
                                interaction=interactions,
                            )
                            modal_findings = await modal_tester.run(
                                url=self.url,
                                page_title=page_title,
                                dialog_info=dialog_info,
                            )
                            for mf in modal_findings:
                                mf.evidence.append(state_screenshot)
                                result.add_finding(mf)
                            self._trace(result, step, "modal_focus_trap_test", {
                                "dialog": dialog_info.get("selector"),
                            }, {
                                "findings": len(modal_findings),
                            })
                    except Exception as modal_exc:
                        log.warning("orchestrator.modal_test_failed", error=str(modal_exc))

                    log.info("orchestrator.step", step=step, action="agentic_rescan", interaction=interactions)
                    await _run_scanners(state_screenshot, suffix=f"_i{interactions}")

            # ── Step 5: Deduplication ──────────────────────────────────────
            step += 1
            log.info("orchestrator.step", step=step, action="deduplicate")
            dedup.deduplicate(result.findings)
            self._trace(result, step, "deduplicate", {}, {
                "total": len(result.findings),
                "duplicates": sum(1 for f in result.findings if f.duplicate_of),
            })

            # ── Step 6: AI Reasoning (optional) ───────────────────────────
            step += 1
            log.info("orchestrator.step", step=step, action="ai_reasoning")
            try:
                from accessibility_agent.ai.reasoning_engine import ReasoningEngine
                reasoning = ReasoningEngine()
                if reasoning.is_enabled:
                    await reasoning.enrich_findings(result.findings)
                    self._trace(result, step, "ai_reasoning", {},
                                {"enriched": len([f for f in result.findings if f.ai_reasoning])})
                else:
                    log.info("orchestrator.ai_skipped", reason="LLM provider disabled")
                    self._trace(result, step, "ai_reasoning", {}, {"status": "disabled"})
            except Exception as exc:
                error_msg = f"AI reasoning failed: {exc}"
                log.error("orchestrator.ai_failed", error=error_msg)
                result.errors.append(error_msg)

            # ── Step 7: Final screenshot ────────────────────────────────────
            final_screenshot = await evidence_collector.capture_screenshot(
                label="final_state",
                full_page=True,
            )

            result.agent_steps = step
            # Store the shared screenshot cache so the report can resolve screenshot references
            result.shared_screenshots = evidence_collector.shared_screenshots
            log.info(
                "orchestrator.screenshots_deduped",
                unique_screenshots=len(result.shared_screenshots),
            )

        # ── Generate Reports ───────────────────────────────────────────────
        report_gen = ReportGenerator(self._output_dir)
        report_paths = report_gen.generate(result)

        log.info(
            "orchestrator.complete",
            run_id=result.run_id,
            findings=result.metrics.total_findings,
            confirmed=result.metrics.confirmed_findings,
            reports={k: str(v) for k, v in report_paths.items()},
        )
        return result

    @staticmethod
    def _trace(
        result: ScanResult,
        step: int,
        action: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
    ) -> None:
        """Append a step to the execution trace for reproducibility."""
        result.execution_trace.append({
            "step": step,
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "inputs": inputs,
            "outputs": outputs,
        })
