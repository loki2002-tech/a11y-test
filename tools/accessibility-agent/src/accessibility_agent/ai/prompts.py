"""
Prompt Templates for AI Accessibility Reasoning.

Rules for all prompts:
1. Always ground the AI in WCAG 2.2 — never let it invent rules.
2. Always instruct the AI to cite specific evidence it used.
3. Always ask for structured JSON output for reliable parsing.
4. Always include an explicit "do not hallucinate" instruction.
5. Keep prompts under 4000 tokens to avoid excessive cost.
"""

from __future__ import annotations

from accessibility_agent.wcag.schemas import Finding


SYSTEM_PROMPT = """You are a senior WCAG 2.2 accessibility engineer with 15 years of experience.
Your job is to analyse accessibility findings detected by automated tools and provide:
- Accurate root cause analysis based on EVIDENCE ONLY
- Developer-ready remediation code
- Plain English explanations for non-technical stakeholders

CRITICAL RULES you must NEVER violate:
1. Only reference WCAG 2.2 success criteria that actually exist (1.1.1 through 4.1.3).
2. Base ALL conclusions on the provided DOM evidence and tool output — never guess.
3. If you are uncertain, say so explicitly. Never fabricate.
4. Always label your reasoning as "AI-Assisted" — it is supplementary, not definitive.
5. Return ONLY valid JSON in the format specified — no markdown, no explanation outside JSON.
"""


def build_reasoning_prompt(finding: Finding) -> str:
    """
    Build the full reasoning prompt for a single finding.
    Includes: rule ID, WCAG reference, DOM HTML, failure summary, selector.
    """
    # Extract the most useful evidence (tool_output from axe)
    tool_evidence = ""
    for ev in finding.evidence:
        if ev.evidence_type == "tool_output":
            tool_evidence = ev.data[:1500]  # Truncate to save tokens
            break

    prompt = f"""Analyse the following accessibility finding detected by axe-core on the page: {finding.url}

## Finding Details
- Rule ID: {finding.rule_id}
- WCAG Success Criterion: {finding.wcag.success_criterion} — {finding.wcag.title} (Level {finding.wcag.level.value})
- Current Status: {finding.status.value}
- Automated Description: {finding.description}
- Actual Problem Observed: {finding.actual_result}
- What WCAG Expects: {finding.expected_result}
- Affected Element (CSS Selector): {finding.element.selector}
- Element HTML: {finding.element.html[:500] if finding.element.html else 'Not available'}

## Raw Tool Evidence (axe-core output)
{tool_evidence if tool_evidence else 'No raw tool evidence available.'}

## Your Task
Return a JSON object with EXACTLY these fields and no others:

{{
  "root_cause": "A single clear sentence explaining WHY this bug exists in the code.",
  "plain_english": "A 1-2 sentence explanation a non-technical manager could understand.",
  "html_fix": "The corrected HTML snippet the developer should use. Be specific and complete.",
  "aria_fix": "Any ARIA attributes to add/remove (empty string if none needed).",
  "testing_guidance": "How a developer can verify their fix is correct (1-2 sentences).",
  "confidence": 0.0,
  "reasoning_notes": "Brief notes on evidence used and any uncertainty."
}}

The "confidence" field must be a number between 0.0 (very uncertain) and 1.0 (highly certain).
Base confidence on how much evidence is available and how clear the violation is.
"""
    return prompt


def build_manual_review_prompt(finding: Finding) -> str:
    """
    Build a prompt for the AI to decide if a manual-review finding is a real bug.
    """
    tool_evidence = ""
    for ev in finding.evidence:
        if ev.evidence_type in ("tool_output", "computed_style"):
            tool_evidence = ev.data[:1200]
            break

    prompt = f"""An automated accessibility scanner flagged the following element for MANUAL REVIEW.
It was UNABLE to automatically determine if this is a real accessibility violation or a false positive.

## Page: {finding.url}
## WCAG Criterion: {finding.wcag.success_criterion} — {finding.wcag.title} (Level {finding.wcag.level.value})
## Rule: {finding.rule_id}
## Automated Message: {finding.description}
## Affected Element: {finding.element.selector}
## Element HTML: {finding.element.html[:400] if finding.element.html else 'Not available'}

## Raw Evidence
{tool_evidence if tool_evidence else 'No raw tool evidence available.'}

## Your Task
Determine if this is likely a real accessibility violation or a false positive.

Return a JSON object with EXACTLY these fields:

{{
  "verdict": "LIKELY_VIOLATION" or "LIKELY_FALSE_POSITIVE" or "CANNOT_DETERMINE",
  "confidence": 0.0,
  "reasoning": "Explain what evidence led to your verdict.",
  "root_cause": "If LIKELY_VIOLATION: why does this fail? If false positive: why is it actually OK?",
  "html_fix": "If LIKELY_VIOLATION: the corrected HTML. Otherwise empty string.",
  "plain_english": "1-2 sentence plain English explanation for a manager."
}}

"confidence" must be between 0.0 and 1.0. Use lower values when evidence is ambiguous.
"""
    return prompt


def build_planner_prompt(html_snippet: str) -> str:
    """
    Build a prompt for the AI to identify up to 5 interactive elements to click.
    """
    prompt = f"""You are an accessibility agent driving an automated browser.
Your goal is to find hidden accessibility bugs by interacting with the page.
Look at the following HTML snippet and identify the CSS selectors of elements that, if clicked, would likely reveal NEW content or mutate the DOM.

Prioritize:
1. Dropdown toggles, accordions, and tabs (e.g., elements with aria-expanded="false", role="tab", or generic buttons).
2. Modal/dialog triggers.
3. Interactive elements that appear to hide content.

Ignore:
1. Standard links that navigate away from the page (a[href] without #).
2. Elements that are purely visual or already expanded.

## HTML Snippet
{html_snippet}

## Your Task
Return a JSON object containing a list of exactly up to 5 CSS selectors to click. The JSON must look like this exactly:
{{
  "selectors": ["#actual-id-from-html", ".actual-class-from-html"]
}}

CRITICAL: Do NOT copy the example selectors above. You MUST extract real CSS selectors that actually exist in the HTML snippet provided. If no interactive elements exist, return an empty list.

If no elements are worth clicking, return an empty list for "selectors".
Do not return markdown, only the JSON object.
"""
    return prompt


def build_batch_reasoning_prompt(findings: list, is_manual_review: bool = False) -> str:
    """
    Build a single prompt for a BATCH of findings.
    The AI returns a JSON array with one result object per finding, in order.
    """
    items = []
    for i, finding in enumerate(findings):
        tool_evidence = ""
        for ev in finding.evidence:
            if ev.evidence_type in ("tool_output", "computed_style"):
                tool_evidence = ev.data[:800]
                break

        items.append(f"""--- Finding #{i+1} ---
Rule: {finding.rule_id}
WCAG: {finding.wcag.success_criterion} — {finding.wcag.title} (Level {finding.wcag.level.value})
Element Selector: {finding.element.selector}
Element HTML: {(finding.element.html or '')[:300]}
Automated Description: {finding.description}
Actual Problem: {finding.actual_result}
Raw Tool Evidence: {tool_evidence or 'Not available'}""")

    findings_block = "\n\n".join(items)

    if not is_manual_review:
        format_example = """{
  "root_cause": "One clear sentence: WHY does this bug exist?",
  "plain_english": "One sentence a non-technical manager understands.",
  "html_fix": "The complete corrected HTML snippet.",
  "aria_fix": "ARIA attributes to add/remove, or empty string.",
  "testing_guidance": "How to verify the fix works.",
  "confidence": 0.9,
  "reasoning_notes": "Brief notes on evidence used."
}"""
        task = f"""For each finding, return ONE JSON object in the array.
Return EXACTLY {len(findings)} objects in the array, in the same order as the findings above."""
    else:
        format_example = """{
  "verdict": "LIKELY_VIOLATION or LIKELY_FALSE_POSITIVE or CANNOT_DETERMINE",
  "confidence": 0.8,
  "reasoning": "Evidence-based explanation of verdict.",
  "root_cause": "If LIKELY_VIOLATION: why does it fail? Otherwise empty.",
  "html_fix": "If LIKELY_VIOLATION: corrected HTML. Otherwise empty.",
  "plain_english": "One sentence plain English explanation."
}"""
        task = f"""Each finding was flagged for manual review. Determine if it is a real violation or a false positive.
Return EXACTLY {len(findings)} objects in the array, in the same order as the findings above."""

    prompt = f"""Analyse the following {len(findings)} accessibility findings from a single automated scan.

{findings_block}

## Your Task
{task}

## Required Output Format
Return ONLY a valid JSON array. No markdown. No explanation outside the array.
Each element of the array must follow this exact schema:
{format_example}

CRITICAL RULES:
1. Only reference WCAG 2.2 success criteria that actually exist.
2. Base ALL conclusions on the evidence provided. Never guess.
3. Return exactly {len(findings)} items in the array, one per finding, in order.
4. Output ONLY the JSON array, nothing else.
"""
    return prompt


def build_test_plan_prompt(ax_tree_summary: str, html_snippet: str) -> str:
    """
    Generate a test plan from the accessibility tree BEFORE any interactions.
    The plan is surfaced in the HTML report to show what the agent intended to test.
    """
    prompt = f"""You are an expert WCAG 2.2 accessibility tester driving an automated browser agent.
Before clicking anything, you must produce a structured test plan.

You are given two sources of information:
1. The ACCESSIBILITY TREE — what screen readers actually experience (roles, names, states).
2. An HTML SNIPPET — the page structure for reference.

## Accessibility Tree
{ax_tree_summary}

## HTML Snippet (first 5000 chars)
{html_snippet}

## Your Task
Identify the most important interactions to perform to discover hidden accessibility bugs ON THE CURRENT PAGE.
Focus on:
- Collapsed accordions, tabs, or dropdowns (aria-expanded="false") — clicking reveals hidden content
- Modal/dialog triggers — test focus trapping and Escape key behavior
- Forms — fill with invalid data to test error message announcements
- Navigation menus — test keyboard tab order (DO NOT click links that navigate away from the page)
- Elements with missing ARIA roles or labels visible in the tree

CRITICAL RULE: Do NOT include steps that click on standard links (`<a>` tags with `href`) that would navigate the browser away from the current URL. You must only interact with elements that mutate the current page.

Return a JSON object with a "test_plan" array. Each item must follow this exact schema:
{{
  "step": 1,
  "action": "click" | "fill_form" | "press_tab" | "run_axe",
  "target": "CSS selector or human-readable description",
  "reason": "Why testing this specific element matters for accessibility",
  "wcag_criteria": ["2.1.1", "4.1.2"]
}}

Return ONLY valid JSON. No markdown. No explanation outside JSON.
Cap the plan at 8 steps maximum.
"""
    return prompt


def build_smart_planner_prompt(
    ax_tree_summary: str,
    html_snippet: str,
    already_clicked: list[str],
) -> str:
    """
    Ask the LLM which element to interact with NEXT, given:
    - The accessibility tree (what screen readers see)
    - The HTML structure for selector reference
    - What has already been clicked (to avoid repeating)
    """
    already_str = ", ".join(already_clicked) if already_clicked else "None yet"

    prompt = f"""You are an AI agent driving an accessibility audit of a live webpage.
Your goal is to discover hidden accessibility bugs by interacting with the page.

You are given:
1. The ACCESSIBILITY TREE — what a screen reader experiences right now.
2. An HTML SNIPPET — use this to find accurate CSS selectors.
3. ALREADY INTERACTED — selectors you have already clicked. Do NOT repeat these.

## Accessibility Tree (what screen readers see)
{ax_tree_summary}

## HTML Snippet
{html_snippet}

## Already Interacted With
{already_str}

## Your Task
Identify the NEXT best interactive elements to click to reveal new accessibility-relevant content.

Prioritize in order:
1. Elements with aria-expanded="false" (collapsed menus, accordions, dropdowns)
2. Elements with role="tab" that are not selected
3. Buttons that likely open modals or dialogs
4. Form elements that may have missing labels or error handling

IGNORE:
- Links (<a> tags) that navigate to other pages (ignore href attributes entirely)
- Elements already listed in "Already Interacted With"
- Submit buttons (handled separately by form tester)

Return a JSON object with a "selectors" array of up to 5 CSS selectors from the actual HTML:
{{
  "selectors": [".real-selector-from-html", "#another-real-id"]
}}

CRITICAL: Only return selectors that actually exist in the HTML snippet above. Do NOT select standard navigation links.
Return ONLY the JSON object. No markdown.
"""
    return prompt
