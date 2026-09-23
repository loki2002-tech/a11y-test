"""
Severity Model Documentation.

IMPORTANT NOTICE
================
The ``ImpactLevel`` field in a Finding is NOT the same as the WCAG conformance level.
These are fundamentally different concepts that must never be conflated.

Definitions
-----------

WCAG Level (A, AA, AAA)
    The conformance threshold defined by the W3C Web Content Accessibility
    Guidelines (WCAG 2.2).  Level A = minimum baseline, AA = mid-level,
    AAA = enhanced.  This is a STANDARD, not a severity.

    Reference: https://www.w3.org/TR/WCAG22/#conformance-reqs

ImpactLevel (critical, serious, moderate, minor)
    A user-impact classification that describes the severity of the user
    experience degradation caused by the finding.  Derived from Deque's
    impact taxonomy used by axe-core.

    Reference: https://github.com/dequelabs/axe-core/blob/develop/doc/impact.md

Relationship
------------
A WCAG Level A violation CAN have a "minor" impact on some users.
A WCAG Level AAA requirement CAN have a "critical" impact on others.
These two scales are ORTHOGONAL.

The system reports BOTH independently.  Never sort or filter findings
using WCAG level as a proxy for severity — use ImpactLevel for that purpose.

Impact Classification
---------------------

critical
    Blocks content or functionality completely for users with disabilities.
    No accessible alternative exists.
    Example: An interactive element that cannot be focused with a keyboard.

serious
    Makes content or functionality significantly more difficult to use.
    May have a partial workaround, but it is unreliable or non-obvious.
    Example: A form input with an insufficient color contrast ratio.

moderate
    Causes some difficulty but a reasonable workaround exists.
    Example: A heading hierarchy that is skipped once.

minor
    Minor inconvenience that doesn't significantly impair usability.
    Example: A redundant alt text that repeats adjacent caption text.
"""
