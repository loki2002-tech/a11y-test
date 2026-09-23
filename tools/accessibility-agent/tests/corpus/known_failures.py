"""
Known-failing accessibility test fixture — WCAG Level A violations.

This HTML file contains deliberately introduced accessibility failures that
the agent MUST detect.  Each failure is documented with the expected:
- WCAG Success Criterion
- axe-core rule ID
- Expected status

Used by the evaluation test suite to measure recall.

DO NOT use this file in production — it is intentionally inaccessible.
"""

KNOWN_FAILURES_HTML = """<!DOCTYPE html>
<html>
<!-- FAIL: 3.1.1 — No lang attribute -->
<head>
    <title></title>
    <!-- FAIL: 2.4.2 — Empty page title -->
</head>
<body>

<!-- FAIL: 1.1.1 — Image with no alt attribute -->
<img src="logo.png">

<!-- FAIL: 1.1.1 — Image with empty alt and not decorative (has visible context suggesting meaning) -->
<img src="error-icon.png" alt="">

<!-- FAIL: 4.1.2 — Button with no accessible name -->
<button></button>

<!-- FAIL: 4.1.2 — Input with no label -->
<form>
    <input type="text" name="username">
    <input type="submit" value="Submit">
</form>

<!-- FAIL: 1.3.1 — Heading used purely for visual styling (skipped level) -->
<h1>Page Title</h1>
<h3>Section (skipped h2)</h3>

<!-- FAIL: 4.1.2 — Link with no accessible name (empty anchor) -->
<a href="/dashboard"></a>

<!-- FAIL: 1.4.3 — Low contrast text (white on near-white) -->
<p style="color: #eeeeee; background: white;">Very low contrast text</p>

<!-- FAIL: 2.4.1 — No skip link -->
<!-- (Absence of skip link — detected via axe bypass rule) -->
<nav>
    <a href="/">Home</a>
    <a href="/about">About</a>
</nav>

<!-- FAIL: 1.3.1 — Table with no headers -->
<table>
    <tr><td>Name</td><td>Email</td></tr>
    <tr><td>Alice</td><td>alice@example.com</td></tr>
</table>

<!-- FAIL: 4.1.2 — ARIA role applied to wrong element -->
<div role="button">Click me</div>
<!-- Note: div role=button without tabindex is also a keyboard failure (2.1.1)
     but axe may not always flag this without tabindex context -->

<!-- FAIL: 1.3.1 — Form error not associated with input -->
<p style="color:red">Please enter a valid email</p>
<input type="email" name="email">

</body>
</html>
"""

KNOWN_FAILURES_EXPECTED = [
    {"rule_id": "html-has-lang",   "sc": "3.1.1", "status": "confirmed"},
    {"rule_id": "document-title",  "sc": "2.4.2", "status": "confirmed"},
    {"rule_id": "image-alt",       "sc": "1.1.1", "status": "confirmed"},
    {"rule_id": "button-name",     "sc": "4.1.2", "status": "confirmed"},
    {"rule_id": "label",           "sc": "1.3.1", "status": "confirmed"},
    {"rule_id": "heading-order",   "sc": "1.3.1", "status": "confirmed"},
    {"rule_id": "link-name",       "sc": "2.4.4", "status": "confirmed"},
    {"rule_id": "color-contrast",  "sc": "1.4.3", "status": "confirmed"},
]
