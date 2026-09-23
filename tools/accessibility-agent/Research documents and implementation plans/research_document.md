# Accessibility Testing Research Document

## 1. Existing Accessibility Testing Approaches
Modern accessibility testing employs a hybrid approach, as full WCAG conformance cannot be verified through automation alone.
- **Deterministic Automated Testing:** Uses engines like Deque's `axe-core`, IBM Equal Access, or Siteimprove to evaluate the DOM against deterministic rules (e.g., W3C ACT Rules). These tools are fast and reliable but only catch ~30-40% of WCAG issues.
- **Browser Automation:** Uses Playwright, Puppeteer, or Selenium to traverse pages, interact with components, and run deterministic engines across different application states.
- **Manual Assistive Technology (AT) Testing:** Human testers use screen readers (NVDA, JAWS, VoiceOver) and keyboard navigation to verify subjective and complex interactive criteria.
- **Heuristic / AI-Assisted Testing:** Emerging tools use Computer Vision and LLMs to evaluate semantics (e.g., is this alt text descriptive?) and visual behavior (e.g., is this custom focus ring perceivable?).

## 2. What Can Be Automated Reliably (AUTOMATED)
- Presence of `alt` attributes on `<img>`
- Valid ARIA role, state, and property usage (syntax and valid values)
- Color contrast for text (when DOM/CSS accurately reflects rendering, though absolute positioning can complicate this)
- Correct heading hierarchy (mathematically)
- Form input label associations (presence of `<label for>` or `aria-labelledby`)
- Presence of document language (`<html lang="en">`)
- Focusable elements having tabindex (basic checks)
- Parsing / duplicate IDs (though obsolete in WCAG 2.1+, still useful for ARIA references)

## 3. What Requires Manual Verification (MANUAL)
- Semantic accuracy (e.g., does this heading structure make logical sense?)
- Meaningfulness of `alt` text and ARIA labels in context
- Focus visibility (when custom outlines or complex stacking contexts are used)
- Focus order logicality (does the tab order match the visual reading order?)
- Screen reader announcements for dynamic state changes (live regions)
- Error identification and suggestion usefulness
- Complex widget interactions (e.g., does this custom combobox behave like a standard combobox to AT?)

## 4. What AI Can Safely Assist With (AI-ASSISTED)
- **Evaluating Alt Text:** Assessing if the existing alt text accurately describes the image contextually.
- **Root-Cause Analysis:** Explaining *why* an automated tool flagged an error based on DOM structure.
- **Remediation Guidance:** Generating specific React/HTML/CSS code snippets to fix identified deterministic failures.
- **Finding Duplicates:** Grouping similar failures across different pages based on component signatures.
- **Visual Validation:** Using Vision Models to check if a bounding box (focused element) has a visible indicator, or if a modal visibly obscures content.
- **Focus Order Logic:** Comparing the DOM tab order sequence against the visual spatial layout to flag potential illogical orders for manual review.

## 5. What AI Must NOT Decide Without Evidence (NOT RELIABLY AUTOMATABLE)
- AI must NOT determine pass/fail for Color Contrast (use deterministic formula).
- AI must NOT invent ARIA states that aren't present in the DOM.
- AI must NOT claim full WCAG 2.2 conformance.
- AI must NOT hallucinate screen reader transcripts (browser accessibility trees are not identical to AT output).

## 6. Available Accessibility APIs & Browser Capabilities
- **Browser Accessibility Tree:** Playwright's `page.accessibility.snapshot()` exposes the browser's computed accessibility tree (Role, Name, Value, Description, State). This is the immediate precursor to AT APIs (UIAutomation, MSAA, AT-SPI).
- **CDP (Chrome DevTools Protocol):** Can be used via Playwright to access `Accessibility.getFullAXTree` for deeper insights, including ignored nodes and reasons.
- **DOM APIs:** `window.getComputedStyle`, Element bounding boxes (`getBoundingClientRect`).

## 7. Limitations of axe-core and Similar Engines
- **Statefulness:** axe-core evaluates a single static DOM state. To test a dropdown, the automation script must first click it, wait for the DOM to update, and run axe again.
- **False Negatives:** axe-core cannot evaluate if an `aria-label="button"` actually describes what the button does. It only knows the attribute exists.
- **Visual Context:** axe-core cannot "see" if a custom focus ring has sufficient contrast against a dynamic gradient background.

## 8. ACT Rule Applicability
Accessibility Conformance Testing (ACT) Rules provide formal, W3C-backed test procedures. Our agent will map deterministic engine findings back to ACT Rules to ensure standardized interpretations of WCAG 2.2 Success Criteria.

## 9. False-Positive and False-Negative Risks
- **False Positives:** Flagging off-screen or `aria-hidden` content that isn't actually exposed to users. AI misinterpreting decorative images as needing alt text.
- **False Negatives:** Relying purely on deterministic scans means missing logical errors (e.g., a "Submit" button labeled as "Cancel" with valid HTML).

## 10. Recommended Architecture
A **Hybrid Orchestrator Model**:
1. Playwright navigates and manipulates the page.
2. deterministic engines (`axe-core`) run continuously at each state change.
3. DOM & Accessibility Tree snapshots are taken alongside screenshots.
4. AI Reasoner reviews the deterministic output, groups it, generates human-readable explanations, and proposes code fixes.
5. AI Vision Agent reviews screenshots for specific criteria (focus visibility, responsive reflow issues) and flags them as `REQUIRES_MANUAL_REVIEW`.
6. Output is strictly schema-validated JSON.
