"""
Known-passing accessibility test fixture.

This HTML file contains correctly implemented accessible patterns.
The agent MUST NOT produce confirmed findings for these elements.

Used to measure false positive rate.
"""

KNOWN_PASSES_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Accessible Page — All Patterns Correct</title>
</head>
<body>

<!-- PASS: 1.1.1 — Meaningful image with descriptive alt -->
<img src="logo.png" alt="Company Logo — returns to homepage">

<!-- PASS: 1.1.1 — Decorative image with empty alt -->
<img src="decorative-divider.png" alt="" role="presentation">

<!-- PASS: 4.1.2 — Button with visible text -->
<button type="button">Save Changes</button>

<!-- PASS: 4.1.2 — Icon button with aria-label -->
<button type="button" aria-label="Close dialog">
    <svg aria-hidden="true" focusable="false"><path d="M..."/></svg>
</button>

<!-- PASS: 1.3.1 / 3.3.2 — Input with associated label -->
<form>
    <label for="username">Username</label>
    <input type="text" id="username" name="username" autocomplete="username">

    <label for="email">Email address</label>
    <input type="email" id="email" name="email" autocomplete="email">

    <button type="submit">Sign in</button>
</form>

<!-- PASS: 1.3.1 — Correct heading hierarchy -->
<h1>Main Page Title</h1>
<h2>First Section</h2>
<h3>Subsection</h3>
<h2>Second Section</h2>

<!-- PASS: 2.4.4 — Link with descriptive text -->
<a href="/privacy">Read our privacy policy</a>

<!-- PASS: 1.3.1 — Table with proper headers -->
<table>
    <caption>Contact Information</caption>
    <thead>
        <tr>
            <th scope="col">Name</th>
            <th scope="col">Email</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td>Alice Smith</td>
            <td>alice@example.com</td>
        </tr>
    </tbody>
</table>

<!-- PASS: 2.4.1 — Skip link -->
<a href="#main-content" class="skip-link">Skip to main content</a>
<main id="main-content">
    <p>Main content here.</p>
</main>

<!-- PASS: 3.1.1 — Lang set on html element (above) -->

<!-- PASS: 1.4.3 — High contrast text (black on white, ratio > 4.5:1) -->
<p style="color:#000000; background:#ffffff;">Normal high-contrast text.</p>

</body>
</html>
"""

KNOWN_PASSES_EXPECTED_NO_VIOLATIONS: list[str] = [
    # These axe rules should NOT fire (confirmed violations) on this page
    "image-alt",
    "button-name",
    "label",
    "heading-order",
    "link-name",
    "html-has-lang",
    "document-title",
    "color-contrast",
]
