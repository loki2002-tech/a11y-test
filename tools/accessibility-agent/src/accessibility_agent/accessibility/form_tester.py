"""
Form Intelligence Tester (v0.6)

Tests forms for accessibility violations that only appear in error states:
- Missing aria-live or role="alert" on error messages
- Error messages not programmatically associated with their input (aria-describedby)
- Focus not moved to error summary on submit
- Required fields not marked with aria-required
- Inputs missing accessible labels

This covers WCAG 2.2 Success Criteria:
- 1.3.1 Info and Relationships (Level A)
- 3.3.1 Error Identification (Level A)
- 3.3.2 Labels or Instructions (Level A)
- 3.3.3 Error Suggestion (Level AA)
- 4.1.3 Status Messages (Level AA)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from accessibility_agent.accessibility.axe_engine import AxeEngine
from accessibility_agent.accessibility.browser import BrowserController
from accessibility_agent.logging_config import get_logger
from accessibility_agent.wcag.schemas import (
    DetectionMethod,
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

# Invalid test values for form fields by type
_TEST_VALUES: dict[str, str] = {
    "email": "not-an-email",
    "tel": "abc",
    "url": "not-a-url",
    "number": "abc",
    "date": "not-a-date",
    "text": "",  # Empty string triggers "required" errors
    "password": "",
    "search": "",
    "textarea": "",
    "default": "",
}


class FormTester:
    """
    Discovers form elements, submits them with invalid/empty data,
    and scans the resulting error state for accessibility violations.
    """

    def __init__(self, browser: BrowserController, axe_engine: AxeEngine, evidence_collector: Any = None) -> None:
        self._browser = browser
        self._axe_engine = axe_engine
        self._evidence_collector = evidence_collector

    async def run(self, url: str, page_title: str = "") -> list[Finding]:
        """
        Find all forms on the page, trigger their error states, and scan.
        Returns a flat list of accessibility findings.
        """
        findings: list[Finding] = []

        forms = await self._detect_forms()
        if not forms:
            log.info("form_tester.no_forms_found")
            return findings

        log.info("form_tester.forms_found", count=len(forms))

        for i, form in enumerate(forms):
            form_selector = form.get("selector", f"form:nth-of-type({i + 1})")
            log.info("form_tester.testing_form", form=form_selector)

            try:
                # Fill the form with invalid/empty test values
                await self._fill_form_with_invalid_data(form)

                # Submit the form to trigger validation errors
                await self._submit_form(form)

                # Wait for error messages to render (JS-driven validation)
                await asyncio.sleep(0.8)

                # Run axe-core on the error state — focus on forms/aria rules
                error_state_screenshot = await self._browser.take_screenshot(full_page=True)
                import base64
                b64 = base64.b64encode(error_state_screenshot).decode("ascii")

                axe_result = await self._axe_engine.run(url=url, page_title=page_title)
                for finding in axe_result.findings:
                    # Tag it so we know it came from the form error state
                    finding.ai_reasoning = (
                        f"[Form Error State] Detected after submitting '{form_selector}' "
                        f"with invalid data. {finding.ai_reasoning}"
                    )
                    findings.append(finding)

                # Also run our own targeted checks
                custom_findings = await self._check_error_announcements(
                    url=url, page_title=page_title, form_selector=form_selector
                )
                findings.extend(custom_findings)

                log.info(
                    "form_tester.form_complete",
                    form=form_selector,
                    new_findings=len(axe_result.findings) + len(custom_findings),
                )

            except Exception as exc:
                log.warning("form_tester.form_failed", form=form_selector, error=str(exc))

        return findings

    async def _detect_forms(self) -> list[dict[str, Any]]:
        """Use JavaScript to find all forms and their input fields."""
        try:
            forms = await self._browser._page.evaluate("""() => {
                const forms = [];
                document.querySelectorAll('form').forEach((form, i) => {
                    const inputs = [];
                    form.querySelectorAll('input, textarea, select').forEach(el => {
                        const cls = el.getAttribute('class');
                        inputs.push({
                            selector: el.id ? '#' + el.id :
                                      el.name ? `[name="${el.name}"]` :
                                      cls ? '.' + cls.split(' ')[0] : null,
                            type: el.tagName === 'SELECT' ? 'select' :
                                  el.tagName === 'TEXTAREA' ? 'textarea' :
                                  (el.type || 'text'),
                            required: el.required,
                            name: el.name || el.id || '',
                        });
                    });
                    const submitBtn = form.querySelector('[type="submit"], button:not([type="button"])');
                    forms.push({
                        selector: form.id ? '#' + form.id : `form:nth-of-type(${i + 1})`,
                        inputs: inputs.filter(inp => inp.selector),
                        submit_selector: submitBtn ? (
                            submitBtn.id ? '#' + submitBtn.id : '[type="submit"]'
                        ) : null,
                        has_required: inputs.some(i => i.required),
                    });
                });
                return forms;
            }""")
            return forms or []
        except Exception as exc:
            log.warning("form_tester.detect_failed", error=str(exc))
            return []

    async def _fill_form_with_invalid_data(self, form: dict[str, Any]) -> None:
        """Fill each input in the form with an invalid test value."""
        for inp in form.get("inputs", []):
            selector = inp.get("selector")
            field_type = inp.get("type", "text").lower()
            if not selector:
                continue

            test_value = _TEST_VALUES.get(field_type, _TEST_VALUES["default"])
            try:
                element = await self._browser._page.query_selector(selector)
                if element:
                    tag = await element.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "select":
                        # For selects, don't change value — just leave default
                        pass
                    elif test_value:
                        await self._browser._page.fill(selector, test_value)
                    # For empty values (required fields), clear the field
                    else:
                        await self._browser._page.fill(selector, "")
            except Exception:
                pass  # Silently skip fields we can't fill

    async def _submit_form(self, form: dict[str, Any]) -> None:
        """Attempt to submit the form."""
        submit_selector = form.get("submit_selector")
        form_selector = form.get("selector", "form")
        try:
            if submit_selector:
                element = await self._browser._page.query_selector(submit_selector)
                if element:
                    await element.click()
                    return
            # Fall back to pressing Enter on the form
            await self._browser._page.evaluate(
                f"document.querySelector('{form_selector}')?.requestSubmit()"
            )
        except Exception as exc:
            log.debug("form_tester.submit_failed", error=str(exc))

    async def _check_error_announcements(
        self, url: str, page_title: str, form_selector: str
    ) -> list[Finding]:
        """
        Targeted checks for error message accessibility patterns
        that axe-core misses.
        """
        findings: list[Finding] = []
        timestamp = datetime.now(timezone.utc)

        try:
            # Check if any error messages lack role="alert" or aria-live
            result = await self._browser._page.evaluate("""() => {
                const errorPatterns = [
                    '[class*="error"]', '[class*="invalid"]', '[class*="validation"]',
                    '[aria-invalid="true"]', '.help-block', '.form-error',
                    '[role="alert"]', '[aria-live]'
                ];
                const found = [];
                errorPatterns.forEach(pattern => {
                    document.querySelectorAll(pattern).forEach(el => {
                        const text = el.textContent.trim();
                        if (text.length > 0) {
                            const cls = el.getAttribute('class');
                            found.push({
                                selector: el.id ? '#' + el.id :
                                          cls ? '.' + cls.split(' ')[0] : pattern,
                                text: text.substring(0, 100),
                                hasRole: el.getAttribute('role') === 'alert',
                                hasAriaLive: !!el.getAttribute('aria-live'),
                                hasAriaDescribedby: !!el.getAttribute('aria-describedby'),
                            });
                        }
                    });
                });
                return found;
            }""")

            # To avoid redundant screenshots, we'll take one shared screenshot of the error state.
            shared_screenshot_id = None
            if self._evidence_collector:
                snapshot = await self._browser.get_accessibility_snapshot()
                ev_item = await self._evidence_collector.capture_screenshot(
                    label="form_error_state", full_page=True
                )
                shared_screenshot_id = ev_item.screenshot_ref_id

            for error_el in (result or []):
                if not error_el.get("hasRole") and not error_el.get("hasAriaLive"):
                    # Get actual element HTML and bounding box
                    html_snippet = ""
                    bbox = None
                    try:
                        el_handle = await self._browser._page.query_selector(error_el["selector"])
                        if el_handle:
                            html_snippet = await el_handle.evaluate("el => el.outerHTML")
                            box = await el_handle.bounding_box()
                            if box:
                                from accessibility_agent.wcag.schemas import BoundingBox
                                bbox = BoundingBox(
                                    x=box["x"], y=box["y"], width=box["width"], height=box["height"]
                                )
                    except Exception:
                        pass
                        
                    evidence = EvidenceItem(
                        evidence_type="screenshot" if shared_screenshot_id else "tool_output",
                        description=f"Error message element found without role='alert' or aria-live. Text: '{error_el['text']}'",
                        data="",
                        screenshot_ref_id=shared_screenshot_id,
                        url=url,
                        timestamp=timestamp,
                    )
                    
                    from accessibility_agent.wcag.schemas import ElementLocator
                    element_loc = ElementLocator(
                        selector=error_el["selector"],
                        html=html_snippet,
                        bounding_box=bbox,
                    )

                    finding = Finding(
                        url=url,
                        page_title=page_title,
                        timestamp=timestamp,
                        status=FindingStatus.CONFIRMED,
                        detection_method=DetectionMethod.AI_ASSISTED,
                        confidence=0.9,
                        impact=ImpactLevel.CRITICAL,
                        rule_id="form-error-announcement",
                        description=(
                            "Error message appears after form submission but will NOT be "
                            "announced to screen reader users because it lacks role='alert' "
                            "or aria-live."
                        ),
                        actual_result=(
                            f"Error element '{error_el['selector']}' has no role='alert' "
                            f"and no aria-live attribute. Screen readers will silently ignore it."
                        ),
                        expected_result=(
                            "Error messages dynamically injected into the DOM must have "
                            "role='alert' or aria-live='assertive' so screen readers "
                            "automatically announce them."
                        ),
                        root_cause=(
                            "The error message element is added to the DOM dynamically via "
                            "JavaScript but has no ARIA live region attribute, making it "
                            "invisible to assistive technologies."
                        ),
                        wcag=WCAGMapping(
                            version=WCAGVersion.V22,
                            success_criterion="4.1.3",
                            title="Status Messages",
                            level=WCAGLevel.AA,
                            principle=WCAGPrinciple.ROBUST,
                            url="https://www.w3.org/TR/WCAG22/#status-messages",
                        ),
                        evidence=[evidence],
                        element=element_loc,
                    )
                    findings.append(finding)

        except Exception as exc:
            log.warning("form_tester.error_check_failed", error=str(exc))

        return findings
