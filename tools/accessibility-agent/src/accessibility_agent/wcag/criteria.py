"""
WCAG 2.2 Success Criteria Reference Data.

Provides a complete, authoritative mapping of every Level A and AA Success
Criterion in WCAG 2.2 to its metadata.  This data is used by the WCAG Mapping
Engine to validate and enrich findings.

Source:
    https://www.w3.org/TR/WCAG22/
    Published: 5 October 2023
    This version corresponds to WCAG 2.2 (W3C Recommendation).

IMPORTANT:
    Do NOT modify SC titles, levels, or principles without verifying against
    the official W3C specification.  Inaccurate WCAG references undermine
    the credibility of the system.
"""

from __future__ import annotations

from typing import TypedDict


class SCEntry(TypedDict):
    title: str
    level: str          # "A", "AA", "AAA"
    principle: str
    url: str
    description: str


# fmt: off
WCAG_22_CRITERIA: dict[str, SCEntry] = {
    # ── Principle 1: Perceivable ───────────────────────────────────────────
    "1.1.1": {
        "title": "Non-text Content",
        "level": "A",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#non-text-content",
        "description": "All non-text content that is presented to the user has a text alternative that serves the equivalent purpose.",
    },
    "1.2.1": {
        "title": "Audio-only and Video-only (Prerecorded)",
        "level": "A",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#audio-only-and-video-only-prerecorded",
        "description": "For prerecorded audio-only and prerecorded video-only media, a text alternative or audio description is provided.",
    },
    "1.2.2": {
        "title": "Captions (Prerecorded)",
        "level": "A",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#captions-prerecorded",
        "description": "Captions are provided for all prerecorded audio content in synchronized media.",
    },
    "1.2.3": {
        "title": "Audio Description or Media Alternative (Prerecorded)",
        "level": "A",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#audio-description-or-media-alternative-prerecorded",
        "description": "An alternative for time-based media or audio description of the prerecorded video content is provided for synchronized media.",
    },
    "1.2.4": {
        "title": "Captions (Live)",
        "level": "AA",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#captions-live",
        "description": "Captions are provided for all live audio content in synchronized media.",
    },
    "1.2.5": {
        "title": "Audio Description (Prerecorded)",
        "level": "AA",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#audio-description-prerecorded",
        "description": "Audio description is provided for all prerecorded video content in synchronized media.",
    },
    "1.3.1": {
        "title": "Info and Relationships",
        "level": "A",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#info-and-relationships",
        "description": "Information, structure, and relationships conveyed through presentation can be programmatically determined or are available in text.",
    },
    "1.3.2": {
        "title": "Meaningful Sequence",
        "level": "A",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#meaningful-sequence",
        "description": "If the sequence in which content is presented affects its meaning, a correct reading sequence can be programmatically determined.",
    },
    "1.3.3": {
        "title": "Sensory Characteristics",
        "level": "A",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#sensory-characteristics",
        "description": "Instructions provided for understanding and operating content do not rely solely on sensory characteristics of components.",
    },
    "1.3.4": {
        "title": "Orientation",
        "level": "AA",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#orientation",
        "description": "Content does not restrict its view and operation to a single display orientation, unless a specific display orientation is essential.",
    },
    "1.3.5": {
        "title": "Identify Input Purpose",
        "level": "AA",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#identify-input-purpose",
        "description": "The purpose of each input field collecting information about the user can be programmatically determined.",
    },
    "1.4.1": {
        "title": "Use of Color",
        "level": "A",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#use-of-color",
        "description": "Color is not used as the only visual means of conveying information, indicating an action, prompting a response, or distinguishing a visual element.",
    },
    "1.4.2": {
        "title": "Audio Control",
        "level": "A",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#audio-control",
        "description": "If any audio on a Web page plays automatically for more than 3 seconds, a mechanism is available to pause or stop the audio.",
    },
    "1.4.3": {
        "title": "Contrast (Minimum)",
        "level": "AA",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#contrast-minimum",
        "description": "The visual presentation of text and images of text has a contrast ratio of at least 4.5:1.",
    },
    "1.4.4": {
        "title": "Resize Text",
        "level": "AA",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#resize-text",
        "description": "Text can be resized without assistive technology up to 200 percent without loss of content or functionality.",
    },
    "1.4.5": {
        "title": "Images of Text",
        "level": "AA",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#images-of-text",
        "description": "If the technologies being used can achieve the visual presentation, text is used to convey information rather than images of text.",
    },
    "1.4.10": {
        "title": "Reflow",
        "level": "AA",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#reflow",
        "description": "Content can be presented without loss of information or functionality, and without requiring scrolling in two dimensions.",
    },
    "1.4.11": {
        "title": "Non-text Contrast",
        "level": "AA",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#non-text-contrast",
        "description": "The visual presentation of UI components and graphical objects has a contrast ratio of at least 3:1.",
    },
    "1.4.12": {
        "title": "Text Spacing",
        "level": "AA",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#text-spacing",
        "description": "No loss of content or functionality occurs when specified text spacing properties are overridden.",
    },
    "1.4.13": {
        "title": "Content on Hover or Focus",
        "level": "AA",
        "principle": "Perceivable",
        "url": "https://www.w3.org/TR/WCAG22/#content-on-hover-or-focus",
        "description": "Where receiving and then removing pointer hover or keyboard focus triggers additional content to become visible, that content is dismissible, hoverable, and persistent.",
    },

    # ── Principle 2: Operable ─────────────────────────────────────────────
    "2.1.1": {
        "title": "Keyboard",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#keyboard",
        "description": "All functionality of the content is operable through a keyboard interface.",
    },
    "2.1.2": {
        "title": "No Keyboard Trap",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#no-keyboard-trap",
        "description": "If keyboard focus can be moved to a component using a keyboard interface, focus can be moved away using only a keyboard interface.",
    },
    "2.1.4": {
        "title": "Character Key Shortcuts",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#character-key-shortcuts",
        "description": "If a keyboard shortcut is implemented using only letter, punctuation, number, or symbol characters, then a mechanism is available to remap or turn it off.",
    },
    "2.2.1": {
        "title": "Timing Adjustable",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#timing-adjustable",
        "description": "For each time limit that is set by the content, the user can turn off, adjust, or extend the time limit.",
    },
    "2.2.2": {
        "title": "Pause, Stop, Hide",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#pause-stop-hide",
        "description": "For moving, blinking, scrolling, or auto-updating information, a mechanism is available to pause, stop, or hide it.",
    },
    "2.3.1": {
        "title": "Three Flashes or Below Threshold",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#three-flashes-or-below-threshold",
        "description": "Web pages do not contain anything that flashes more than three times in any one second period.",
    },
    "2.4.1": {
        "title": "Bypass Blocks",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#bypass-blocks",
        "description": "A mechanism is available to bypass blocks of content that are repeated on multiple Web pages.",
    },
    "2.4.2": {
        "title": "Page Titled",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#page-titled",
        "description": "Web pages have titles that describe topic or purpose.",
    },
    "2.4.3": {
        "title": "Focus Order",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#focus-order",
        "description": "If a Web page can be navigated sequentially and the navigation sequences affect meaning or operation, focusable components receive focus in an order that preserves meaning and operation.",
    },
    "2.4.4": {
        "title": "Link Purpose (In Context)",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#link-purpose-in-context",
        "description": "The purpose of each link can be determined from the link text alone, or from the link text together with its programmatically determined link context.",
    },
    "2.4.5": {
        "title": "Multiple Ways",
        "level": "AA",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#multiple-ways",
        "description": "More than one way is available to locate a Web page within a set of Web pages.",
    },
    "2.4.6": {
        "title": "Headings and Labels",
        "level": "AA",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#headings-and-labels",
        "description": "Headings and labels describe topic or purpose.",
    },
    "2.4.7": {
        "title": "Focus Visible",
        "level": "AA",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#focus-visible",
        "description": "Any keyboard operable user interface has a mode of operation where the keyboard focus indicator is visible.",
    },
    "2.4.11": {
        "title": "Focus Not Obscured (Minimum)",
        "level": "AA",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#focus-not-obscured-minimum",
        "description": "When a user interface component receives keyboard focus, the component is not entirely hidden due to an author-created sticky header or footer.",
    },
    "2.4.12": {
        "title": "Focus Not Obscured (Enhanced)",
        "level": "AAA",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#focus-not-obscured-enhanced",
        "description": "When a user interface component receives keyboard focus, no part of the component is hidden due to an author-created sticky header or footer.",
    },
    "2.4.13": {
        "title": "Focus Appearance",
        "level": "AAA",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#focus-appearance",
        "description": "When the keyboard focus indicator is visible, an area of the focus indicator meets size and contrast requirements.",
    },
    "2.5.1": {
        "title": "Pointer Gestures",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#pointer-gestures",
        "description": "All functionality that uses multipoint or path-based gestures for operation can be operated with a single pointer.",
    },
    "2.5.2": {
        "title": "Pointer Cancellation",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#pointer-cancellation",
        "description": "For functionality that can be operated using a single pointer, at least one of: no down-event, abort/undo, up reversal, or essential.",
    },
    "2.5.3": {
        "title": "Label in Name",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#label-in-name",
        "description": "For user interface components with labels that include text or images of text, the name contains the text that is presented visually.",
    },
    "2.5.4": {
        "title": "Motion Actuation",
        "level": "A",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#motion-actuation",
        "description": "Functionality that can be operated by device motion or user motion can also be operated by user interface components.",
    },
    "2.5.7": {
        "title": "Dragging Movements",
        "level": "AA",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#dragging-movements",
        "description": "All functionality that uses a dragging movement for operation can be achieved by a single pointer without dragging.",
    },
    "2.5.8": {
        "title": "Target Size (Minimum)",
        "level": "AA",
        "principle": "Operable",
        "url": "https://www.w3.org/TR/WCAG22/#target-size-minimum",
        "description": "The size of the target for pointer inputs is at least 24 by 24 CSS pixels.",
    },

    # ── Principle 3: Understandable ───────────────────────────────────────
    "3.1.1": {
        "title": "Language of Page",
        "level": "A",
        "principle": "Understandable",
        "url": "https://www.w3.org/TR/WCAG22/#language-of-page",
        "description": "The default human language of each Web page can be programmatically determined.",
    },
    "3.1.2": {
        "title": "Language of Parts",
        "level": "AA",
        "principle": "Understandable",
        "url": "https://www.w3.org/TR/WCAG22/#language-of-parts",
        "description": "The human language of each passage or phrase in the content can be programmatically determined.",
    },
    "3.2.1": {
        "title": "On Focus",
        "level": "A",
        "principle": "Understandable",
        "url": "https://www.w3.org/TR/WCAG22/#on-focus",
        "description": "If any component receives focus, it does not initiate a change of context.",
    },
    "3.2.2": {
        "title": "On Input",
        "level": "A",
        "principle": "Understandable",
        "url": "https://www.w3.org/TR/WCAG22/#on-input",
        "description": "Changing the setting of any user interface component does not automatically cause a change of context.",
    },
    "3.2.3": {
        "title": "Consistent Navigation",
        "level": "AA",
        "principle": "Understandable",
        "url": "https://www.w3.org/TR/WCAG22/#consistent-navigation",
        "description": "Navigational mechanisms that are repeated on multiple Web pages occur in the same relative order each time they are repeated.",
    },
    "3.2.4": {
        "title": "Consistent Identification",
        "level": "AA",
        "principle": "Understandable",
        "url": "https://www.w3.org/TR/WCAG22/#consistent-identification",
        "description": "Components that have the same functionality within a set of Web pages are identified consistently.",
    },
    "3.2.6": {
        "title": "Consistent Help",
        "level": "A",
        "principle": "Understandable",
        "url": "https://www.w3.org/TR/WCAG22/#consistent-help",
        "description": "If a Web page contains help mechanisms, those mechanisms occur in the same relative order.",
    },
    "3.3.1": {
        "title": "Error Identification",
        "level": "A",
        "principle": "Understandable",
        "url": "https://www.w3.org/TR/WCAG22/#error-identification",
        "description": "If an input error is automatically detected, the item that is in error is identified and the error is described to the user in text.",
    },
    "3.3.2": {
        "title": "Labels or Instructions",
        "level": "A",
        "principle": "Understandable",
        "url": "https://www.w3.org/TR/WCAG22/#labels-or-instructions",
        "description": "Labels or instructions are provided when content requires user input.",
    },
    "3.3.3": {
        "title": "Error Suggestion",
        "level": "AA",
        "principle": "Understandable",
        "url": "https://www.w3.org/TR/WCAG22/#error-suggestion",
        "description": "If an input error is automatically detected and suggestions for correction are known, then the suggestion is provided to the user.",
    },
    "3.3.4": {
        "title": "Error Prevention (Legal, Financial, Data)",
        "level": "AA",
        "principle": "Understandable",
        "url": "https://www.w3.org/TR/WCAG22/#error-prevention-legal-financial-data",
        "description": "For Web pages that cause legal commitments or financial transactions, submissions are reversible, checked, or confirmed.",
    },
    "3.3.7": {
        "title": "Redundant Entry",
        "level": "A",
        "principle": "Understandable",
        "url": "https://www.w3.org/TR/WCAG22/#redundant-entry",
        "description": "Information previously entered by or provided to the user that is required to be entered again in the same process is either auto-populated or available for the user to select.",
    },
    "3.3.8": {
        "title": "Accessible Authentication (Minimum)",
        "level": "AA",
        "principle": "Understandable",
        "url": "https://www.w3.org/TR/WCAG22/#accessible-authentication-minimum",
        "description": "A cognitive function test is not required for any step in an authentication process unless alternatives or assistance is available.",
    },

    # ── Principle 4: Robust ───────────────────────────────────────────────
    "4.1.1": {
        "title": "Parsing",
        "level": "A",
        "principle": "Robust",
        "url": "https://www.w3.org/TR/WCAG22/#parsing",
        "description": "In WCAG 2.2, this criterion is obsolete and always passes. Retained for mapping legacy findings.",
    },
    "4.1.2": {
        "title": "Name, Role, Value",
        "level": "A",
        "principle": "Robust",
        "url": "https://www.w3.org/TR/WCAG22/#name-role-value",
        "description": "For all user interface components, the name and role can be programmatically determined; states, properties, and values can be programmatically determined and set.",
    },
    "4.1.3": {
        "title": "Status Messages",
        "level": "AA",
        "principle": "Robust",
        "url": "https://www.w3.org/TR/WCAG22/#status-messages",
        "description": "In content implemented using markup languages, status messages can be programmatically determined through role or properties so that they can be presented to the user by assistive technologies.",
    },
}
# fmt: on


def get_sc(criterion: str) -> SCEntry | None:
    """Return the SCEntry for a given criterion string (e.g. '1.1.1')."""
    return WCAG_22_CRITERIA.get(criterion)


def validate_sc(criterion: str) -> bool:
    """Return True if the criterion exists in the WCAG 2.2 reference data."""
    return criterion in WCAG_22_CRITERIA
