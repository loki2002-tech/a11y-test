"""
Source Locator — maps an accessibility Finding to the responsible source file.

This is the most critical component of the Remediation Agent.  It bridges the
gap between the browser DOM (what axe-core sees) and the actual source code
(what a developer must change).

IMPORTANT PRINCIPLE
-------------------
The browser DOM is NOT the source code.
- A React component renders to a DOM element — the source is .tsx/.jsx
- A Django template renders to HTML — the source is a .html template file
- A CSS class in the DOM may be defined in a .scss or styled-components file
- A bundler may inline or minify templates entirely

This module NEVER assumes identity between the DOM and the source.

LOCATOR STRATEGIES (applied in ranked order)
--------------------------------------------
1. exact_class_search        — Search for exact CSS class names from the selector
2. exact_id_search           — Search for exact ID from the selector
3. aria_attribute_search     — Search for aria-label, aria-labelledby values
4. accessible_name_search    — Search for visible text content of the element
5. selector_decomposition    — Break complex selectors into component parts
6. component_name_inference  — Infer component filename from BEM/CSS-module class
7. role_attribute_search     — Search for role="..." attributes in source
8. html_snippet_search       — Search for distinctive sub-strings of element.html
9. framework_specific        — Framework-aware heuristics (JSX, Vue SFC, etc.)

SEARCH BACKEND
--------------
Uses ripgrep (rg) if available — orders of magnitude faster than Python glob.
Falls back to a pure Python pathlib + re search if rg is not installed.
The results are identical regardless of which backend is used.

CONFIDENCE MODEL
----------------
DIRECT_MATCH   — One file, one location, exact attribute match
LIKELY_MATCH   — 1-2 files, strong evidence but not exact
AMBIGUOUS_MATCH — 3+ files or 3+ locations in one file
NOT_FOUND      — No strategy produced a result
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from accessibility_agent.logging_config import get_logger
from accessibility_agent.remediation.schemas import (
    ApplicationFramework,
    SourceLocation,
    SourceMatchConfidence,
)

log = get_logger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

# Extensions searched per framework (ordered by priority)
_EXTENSIONS_BY_FRAMEWORK: dict[ApplicationFramework, list[str]] = {
    ApplicationFramework.STATIC_HTML: [".html", ".htm", ".css", ".scss"],
    ApplicationFramework.REACT: [".tsx", ".jsx", ".ts", ".js", ".css", ".scss", ".module.css"],
    ApplicationFramework.NEXT_JS: [".tsx", ".jsx", ".ts", ".js", ".css", ".scss", ".module.css"],
    ApplicationFramework.VUE: [".vue", ".js", ".ts", ".css", ".scss"],
    ApplicationFramework.NUXT: [".vue", ".js", ".ts", ".css", ".scss"],
    ApplicationFramework.ANGULAR: [".component.html", ".html", ".ts", ".css", ".scss"],
    ApplicationFramework.SVELTE: [".svelte", ".js", ".ts", ".css"],
    ApplicationFramework.DJANGO: [".html", ".py", ".css", ".scss"],
    ApplicationFramework.FLASK: [".html", ".py", ".css", ".scss"],
    ApplicationFramework.RAILS: [".erb", ".html.erb", ".rb", ".css", ".scss"],
    ApplicationFramework.LARAVEL: [".blade.php", ".php", ".css", ".scss"],
    ApplicationFramework.UNKNOWN: [
        ".html", ".htm", ".tsx", ".jsx", ".ts", ".js",
        ".vue", ".svelte", ".py", ".erb", ".php",
        ".css", ".scss", ".sass",
    ],
}

# Directories that should never be searched
_EXCLUDED_DIRS = {
    "node_modules", ".git", "dist", "build", ".next", ".nuxt",
    "__pycache__", ".cache", "coverage", ".pytest_cache", "venv",
    ".venv", "env", ".tox", "vendor", "bower_components",
}

# Maximum number of candidates to collect before ranking
_MAX_CANDIDATES = 50

# Number of context lines to capture around a match
_CONTEXT_LINES = 15


# ── Internal Match Dataclass ──────────────────────────────────────────────────


@dataclass
class _RawMatch:
    """Internal representation of a single file + line match."""

    file_path: Path
    line_number: int
    line_content: str
    strategy: str
    score: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


# ── Framework Detector ────────────────────────────────────────────────────────


class FrameworkDetector:
    """
    Detects the front-end framework of a repository by inspecting its
    file system structure and configuration files.

    Detection is heuristic-based and deterministic — no LLM calls.
    """

    def detect(self, repo_path: Path) -> ApplicationFramework:
        """
        Return the most likely framework for the given repository.

        Checks are ordered from most-specific to least-specific so that
        sub-frameworks (Next.js → React) are correctly identified.
        """
        if not repo_path.is_dir():
            log.warning("framework_detector.repo_not_found", path=str(repo_path))
            return ApplicationFramework.UNKNOWN

        package_json = self._read_package_json(repo_path)

        # ── JavaScript / TypeScript frameworks ────────────────────────────
        if package_json:
            deps = {
                **package_json.get("dependencies", {}),
                **package_json.get("devDependencies", {}),
            }

            # Next.js (must check before React — Next IS React)
            if "next" in deps:
                log.info("framework_detector.detected", framework="next_js")
                return ApplicationFramework.NEXT_JS

            # Nuxt (must check before Vue — Nuxt IS Vue)
            if "nuxt" in deps or "@nuxt/core" in deps:
                log.info("framework_detector.detected", framework="nuxt")
                return ApplicationFramework.NUXT

            # React
            if "react" in deps or "react-dom" in deps:
                log.info("framework_detector.detected", framework="react")
                return ApplicationFramework.REACT

            # Vue
            if "vue" in deps or "@vue/core" in deps:
                log.info("framework_detector.detected", framework="vue")
                return ApplicationFramework.VUE

            # Angular
            if "@angular/core" in deps:
                log.info("framework_detector.detected", framework="angular")
                return ApplicationFramework.ANGULAR

            # Svelte
            if "svelte" in deps or "@sveltejs/kit" in deps:
                log.info("framework_detector.detected", framework="svelte")
                return ApplicationFramework.SVELTE

        # ── Python frameworks ──────────────────────────────────────────────
        if (repo_path / "manage.py").exists():
            log.info("framework_detector.detected", framework="django")
            return ApplicationFramework.DJANGO

        if self._has_flask_markers(repo_path):
            log.info("framework_detector.detected", framework="flask")
            return ApplicationFramework.FLASK

        # ── Ruby on Rails ──────────────────────────────────────────────────
        if (repo_path / "config" / "routes.rb").exists():
            log.info("framework_detector.detected", framework="rails")
            return ApplicationFramework.RAILS

        # ── Laravel ───────────────────────────────────────────────────────
        if (repo_path / "artisan").exists():
            log.info("framework_detector.detected", framework="laravel")
            return ApplicationFramework.LARAVEL

        # ── Static HTML ───────────────────────────────────────────────────
        html_files = list(repo_path.rglob("*.html"))
        if html_files and not package_json:
            log.info("framework_detector.detected", framework="static_html")
            return ApplicationFramework.STATIC_HTML

        log.info("framework_detector.detected", framework="unknown")
        return ApplicationFramework.UNKNOWN

    def _read_package_json(self, repo_path: Path) -> dict[str, Any] | None:
        """Safely read and parse package.json if it exists."""
        pkg = repo_path / "package.json"
        if not pkg.exists():
            return None
        try:
            return json.loads(pkg.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _has_flask_markers(self, repo_path: Path) -> bool:
        """Check for common Flask project markers."""
        markers = ["app.py", "wsgi.py", "application.py"]
        for m in markers:
            if (repo_path / m).exists():
                content = (repo_path / m).read_text(encoding="utf-8", errors="ignore")
                if "flask" in content.lower():
                    return True
        return False


# ── Source Locator ────────────────────────────────────────────────────────────


class SourceLocator:
    """
    Maps an accessibility Finding to its responsible source file and line numbers.

    Usage:
        locator = SourceLocator(repo_path=Path("./my-app"))
        location = locator.locate(finding)

        if location.is_actionable:
            # Proceed to source analysis and patch generation
            pass
        else:
            # Generate manual review package
            pass

    The locator applies 9 strategies in parallel, aggregates all candidate
    matches, ranks them by confidence score, and returns the best result.
    """

    def __init__(
        self,
        repo_path: Path,
        framework: ApplicationFramework | None = None,
    ) -> None:
        self._repo = repo_path.resolve()
        if not self._repo.is_dir():
            raise ValueError(f"Repository path does not exist: {self._repo}")

        self._detector = FrameworkDetector()
        self._framework = framework or self._detector.detect(self._repo)
        self._extensions = _EXTENSIONS_BY_FRAMEWORK.get(self._framework, _EXTENSIONS_BY_FRAMEWORK[ApplicationFramework.UNKNOWN])
        self._ripgrep_available = self._check_ripgrep()

        log.info(
            "source_locator.initialized",
            repo=str(self._repo),
            framework=self._framework.value,
            search_backend="ripgrep" if self._ripgrep_available else "python",
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def locate(self, finding_data: dict[str, Any]) -> SourceLocation:
        """
        Main entry point.  Given a finding dict (or Finding.model_dump()),
        runs all strategies and returns the best SourceLocation.

        Args:
            finding_data: Dict with at minimum:
                - element.selector  (CSS selector)
                - element.html      (outer HTML snippet)
                - element.role
                - element.accessible_name
                - element.text_content
                - description       (issue description)

        Returns:
            SourceLocation with confidence level and all context.
        """
        element = finding_data.get("element", {})
        selector = element.get("selector", "")
        html_snippet = element.get("html", "")
        role = element.get("role", "")
        accessible_name = element.get("accessible_name", "")
        text_content = element.get("text_content", "")
        description = finding_data.get("description", "")
        finding_id = finding_data.get("finding_id", "UNKNOWN")

        log.info(
            "source_locator.locate_start",
            finding_id=finding_id,
            selector=selector[:80],
            framework=self._framework.value,
        )

        candidates: list[_RawMatch] = []

        # ── Run all strategies ────────────────────────────────────────────
        candidates += self._strategy_exact_class_search(selector)
        candidates += self._strategy_exact_id_search(selector)
        candidates += self._strategy_aria_attribute_search(html_snippet, accessible_name)
        candidates += self._strategy_accessible_name_search(accessible_name, text_content)
        candidates += self._strategy_selector_decomposition(selector)
        candidates += self._strategy_component_name_inference(selector)
        candidates += self._strategy_role_attribute_search(role, html_snippet)
        candidates += self._strategy_html_snippet_search(html_snippet)
        candidates += self._strategy_framework_specific(selector, html_snippet, role)

        # ── Remove duplicates and rank ────────────────────────────────────
        candidates = self._deduplicate_candidates(candidates)
        candidates = sorted(candidates, key=lambda m: m.score, reverse=True)
        candidates = candidates[:_MAX_CANDIDATES]

        log.info(
            "source_locator.candidates_found",
            finding_id=finding_id,
            total=len(candidates),
        )

        if not candidates:
            log.warning("source_locator.not_found", finding_id=finding_id)
            return self._not_found_result()

        # ── Determine confidence from candidate distribution ───────────────
        best = candidates[0]
        all_files = list({str(c.file_path) for c in candidates})

        confidence = self._compute_confidence(candidates)

        # ── Build SourceLocation ──────────────────────────────────────────
        context_before, matched_text, context_after = self._read_context(
            best.file_path, best.line_number
        )

        location = SourceLocation(
            file_path=str(best.file_path.relative_to(self._repo)),
            start_line=max(1, best.line_number - 2),
            end_line=best.line_number + 2,
            language=best.file_path.suffix.lstrip("."),
            confidence=confidence,
            framework=self._framework,
            matched_text=matched_text,
            context_before=context_before,
            context_after=context_after,
            search_strategy=best.strategy,
            match_score=best.score,
            all_candidates=[f for f in all_files if f != str(best.file_path.relative_to(self._repo))],
        )

        log.info(
            "source_locator.located",
            finding_id=finding_id,
            file=location.file_path,
            line=best.line_number,
            confidence=confidence.value,
            strategy=best.strategy,
        )

        return location

    # ── Strategy 1: Exact CSS class names ─────────────────────────────────────

    def _strategy_exact_class_search(self, selector: str) -> list[_RawMatch]:
        """Search for exact CSS class names extracted from the selector."""
        classes = self._extract_classes_from_selector(selector)
        results: list[_RawMatch] = []

        for cls in classes:
            if len(cls) < 3:  # Skip trivially short class names
                continue
            # Search for the class in various syntaxes:
            # HTML:    class="register-btn"
            # React:   className="register-btn"
            # Vue:     :class="..." or class="..."
            # CSS:     .register-btn {
            patterns = [
                f'class="{cls}"',
                f"class='{cls}'",
                f'className="{cls}"',
                f"className='{cls}'",
                f".{cls}",
                f'class="{cls} ',     # class is first in a list
                f' {cls}"',           # class is last in a list
                f' {cls} ',           # class is in the middle of a list
            ]
            for pattern in patterns:
                matches = self._search_files(pattern, self._extensions)
                for m in matches:
                    m.strategy = "exact_class_search"
                    m.score = 0.95 if '="' in pattern else 0.75
                    results.append(m)

        return results

    # ── Strategy 2: Exact ID ───────────────────────────────────────────────────

    def _strategy_exact_id_search(self, selector: str) -> list[_RawMatch]:
        """Search for exact element ID extracted from the selector."""
        ids = re.findall(r"#([a-zA-Z0-9_-]+)", selector)
        results: list[_RawMatch] = []

        for elem_id in ids:
            if len(elem_id) < 2:
                continue
            patterns = [
                f'id="{elem_id}"',
                f"id='{elem_id}'",
                f'for="{elem_id}"',       # label for=
                f"htmlFor=\"{elem_id}\"", # React htmlFor
            ]
            for pattern in patterns:
                matches = self._search_files(pattern, self._extensions)
                for m in matches:
                    m.strategy = "exact_id_search"
                    m.score = 0.98  # IDs are unique; very high confidence
                    results.append(m)

        return results

    # ── Strategy 3: ARIA attribute search ─────────────────────────────────────

    def _strategy_aria_attribute_search(
        self, html_snippet: str, accessible_name: str
    ) -> list[_RawMatch]:
        """Search for aria-label, aria-labelledby values from the element."""
        results: list[_RawMatch] = []

        # Extract aria-label value
        aria_label_match = re.search(
            r'aria-label\s*=\s*["\']([^"\']{3,})["\']', html_snippet
        )
        if aria_label_match:
            val = aria_label_match.group(1)
            matches = self._search_files(f'aria-label="{val}"', self._extensions)
            for m in matches:
                m.strategy = "aria_attribute_search"
                m.score = 0.90
                results.append(m)

        # Use accessible_name as a search term if non-trivial
        if accessible_name and len(accessible_name) > 3 and accessible_name not in (
            "button", "link", "image", "input", "checkbox", "radio"
        ):
            for pattern in [f'aria-label="{accessible_name}"', f">{accessible_name}<"]:
                matches = self._search_files(pattern, self._extensions)
                for m in matches:
                    m.strategy = "aria_attribute_search"
                    m.score = 0.80
                    results.append(m)

        return results

    # ── Strategy 4: Accessible name / text content ─────────────────────────────

    def _strategy_accessible_name_search(
        self, accessible_name: str, text_content: str
    ) -> list[_RawMatch]:
        """Search for visible text content of the element."""
        results: list[_RawMatch] = []

        for text in [accessible_name, text_content]:
            if not text or len(text) < 4 or len(text) > 80:
                continue
            # Skip generic words that would produce too many false positives
            if text.lower() in {
                "submit", "button", "click", "here", "more", "next",
                "back", "close", "open", "menu", "home", "link",
            }:
                continue

            # Search for exact text content in source
            matches = self._search_files(text, self._extensions)
            for m in matches:
                m.strategy = "accessible_name_search"
                m.score = 0.65
                results.append(m)

        return results

    # ── Strategy 5: Selector decomposition ────────────────────────────────────

    def _strategy_selector_decomposition(self, selector: str) -> list[_RawMatch]:
        """
        Break complex CSS selectors into meaningful parts and search individually.

        Examples:
            ".Nav__voteBtn--active" → ["Nav", "voteBtn"]
            ".portal-header .nav-link" → ["portal-header", "nav-link"]
            "button.btn.btn-primary" → ["btn-primary", "btn"]
        """
        results: list[_RawMatch] = []

        # Extract all class names and IDs
        raw_classes = re.findall(r"[.#]([a-zA-Z][a-zA-Z0-9_-]+)", selector)

        for cls in raw_classes:
            # Split BEM-style classes: "Nav__voteBtn--active" → ["Nav", "voteBtn", "active"]
            parts = re.split(r"__|--|(?=[A-Z])", cls)
            parts = [p for p in parts if len(p) >= 3]

            for part in parts:
                if part.lower() in {"btn", "active", "primary", "secondary", "wrapper", "container"}:
                    continue  # Too generic
                matches = self._search_files(part, self._extensions)
                for m in matches:
                    m.strategy = "selector_decomposition"
                    m.score = 0.55
                    results.append(m)

        return results

    # ── Strategy 6: Component name inference ──────────────────────────────────

    def _strategy_component_name_inference(self, selector: str) -> list[_RawMatch]:
        """
        Infer a React/Vue component filename from a CSS class name.

        CSS Modules / BEM patterns often encode the component name:
        - ".Register_button__abc123" → component is Register
        - ".NavBar__link" → component is NavBar
        - ".hero-section" → might be HeroSection.tsx
        """
        results: list[_RawMatch] = []

        # CSS Module pattern: ComponentName_elementName__hash
        css_module_match = re.search(r"\.([A-Z][a-zA-Z]+)_", selector)
        if css_module_match:
            component_name = css_module_match.group(1)
            # Search for that component file
            component_files = list(self._repo.rglob(f"{component_name}.*"))
            component_files += list(self._repo.rglob(f"{component_name.lower()}.*"))
            for cf in component_files:
                if cf.suffix in {".tsx", ".jsx", ".vue", ".svelte", ".py", ".html"}:
                    if not self._is_excluded(cf):
                        results.append(_RawMatch(
                            file_path=cf,
                            line_number=1,
                            line_content=f"[Component file: {cf.name}]",
                            strategy="component_name_inference",
                            score=0.70,
                        ))

        # PascalCase in class names often maps to component names
        pascal_matches = re.findall(r"\.([A-Z][a-z]+(?:[A-Z][a-z]+)+)", selector)
        for name in pascal_matches:
            for ext in [".tsx", ".jsx", ".vue", ".svelte"]:
                candidate = self._find_file_by_name(name + ext)
                if candidate:
                    results.append(_RawMatch(
                        file_path=candidate,
                        line_number=1,
                        line_content=f"[Inferred component: {candidate.name}]",
                        strategy="component_name_inference",
                        score=0.68,
                    ))

        return results

    # ── Strategy 7: Role + attribute search ───────────────────────────────────

    def _strategy_role_attribute_search(
        self, role: str, html_snippet: str
    ) -> list[_RawMatch]:
        """Search for role="..." and key HTML attributes from the element."""
        results: list[_RawMatch] = []

        if role and role not in ("generic", "none", "presentation"):
            # Search for role="button" etc. — useful for custom ARIA widgets
            for pattern in [f'role="{role}"', f"role='{role}'"]:
                matches = self._search_files(pattern, self._extensions)
                for m in matches:
                    m.strategy = "role_attribute_search"
                    m.score = 0.45  # Roles are often reused, so lower confidence
                    results.append(m)

        return results

    # ── Strategy 8: HTML snippet search ───────────────────────────────────────

    def _strategy_html_snippet_search(self, html_snippet: str) -> list[_RawMatch]:
        """
        Extract distinctive sub-strings from the element's outer HTML and search.

        Looks for unique attribute combinations that are unlikely to appear
        in more than one place in the source.
        """
        results: list[_RawMatch] = []
        if not html_snippet or len(html_snippet) < 10:
            return results

        # Extract distinctive attribute values (data-*, name=, type= etc.)
        attr_patterns = re.findall(
            r'(?:data-[a-z-]+|name|type|placeholder|for)\s*=\s*["\']([^"\']{3,30})["\']',
            html_snippet,
            re.IGNORECASE,
        )

        for val in attr_patterns[:3]:  # Limit to first 3 to avoid noise
            matches = self._search_files(val, self._extensions)
            for m in matches:
                m.strategy = "html_snippet_search"
                m.score = 0.60
                results.append(m)

        return results

    # ── Strategy 9: Framework-specific ────────────────────────────────────────

    def _strategy_framework_specific(
        self, selector: str, html_snippet: str, role: str
    ) -> list[_RawMatch]:
        """Apply framework-aware heuristics for better matching."""
        results: list[_RawMatch] = []

        classes = self._extract_classes_from_selector(selector)

        if self._framework in (ApplicationFramework.REACT, ApplicationFramework.NEXT_JS):
            # In React, class → className
            for cls in classes:
                if len(cls) >= 3:
                    matches = self._search_files(f"className.*{cls}", [".tsx", ".jsx", ".ts", ".js"])
                    for m in matches:
                        m.strategy = "framework_react_classname"
                        m.score = 0.88
                        results.append(m)

        elif self._framework in (ApplicationFramework.VUE, ApplicationFramework.NUXT):
            # In Vue SFCs, look inside <template> blocks
            for cls in classes:
                if len(cls) >= 3:
                    matches = self._search_files(cls, [".vue"])
                    for m in matches:
                        m.strategy = "framework_vue_template"
                        m.score = 0.85
                        results.append(m)

        elif self._framework == ApplicationFramework.ANGULAR:
            # Angular uses component HTML templates
            for cls in classes:
                if len(cls) >= 3:
                    matches = self._search_files(cls, [".component.html", ".html"])
                    for m in matches:
                        m.strategy = "framework_angular_template"
                        m.score = 0.82
                        results.append(m)

        elif self._framework in (ApplicationFramework.DJANGO, ApplicationFramework.FLASK):
            # Python templates
            for cls in classes:
                if len(cls) >= 3:
                    matches = self._search_files(cls, [".html"])
                    for m in matches:
                        m.strategy = "framework_python_template"
                        m.score = 0.80
                        results.append(m)

        return results

    # ── Search backends ───────────────────────────────────────────────────────

    def _search_files(
        self,
        pattern: str,
        extensions: list[str] | None = None,
    ) -> list[_RawMatch]:
        """
        Search all source files for a text pattern.
        Uses ripgrep if available, falls back to pure Python.
        """
        if not pattern or len(pattern) < 2:
            return []

        exts = extensions or self._extensions

        try:
            if self._ripgrep_available:
                return self._search_with_ripgrep(pattern, exts)
            else:
                return self._search_with_python(pattern, exts)
        except Exception as exc:
            log.debug("source_locator.search_error", pattern=pattern[:40], error=str(exc))
            return []

    def _search_with_ripgrep(
        self, pattern: str, extensions: list[str]
    ) -> list[_RawMatch]:
        """Use ripgrep for fast multi-file search."""
        ext_args: list[str] = []
        for ext in extensions:
            ext_clean = ext.lstrip(".")
            ext_args += ["-t", ext_clean] if not ext_clean.startswith(".") else [
                "--glob", f"*{ext}"
            ]

        glob_args = []
        for ext in extensions:
            glob_args += ["--glob", f"*{ext}"]

        cmd = [
            "rg",
            "--line-number",
            "--no-heading",
            "--fixed-strings",     # Literal search, not regex
            "--max-count", "20",   # Max 20 matches per file
            "--max-filesize", "1M",
            "--encoding", "utf-8",
        ] + glob_args + [pattern, str(self._repo)]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )

        matches: list[_RawMatch] = []
        for line in result.stdout.splitlines():
            # ripgrep output format: path:line_number:content
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            file_path = Path(parts[0])
            if self._is_excluded(file_path):
                continue
            try:
                line_num = int(parts[1])
                content = parts[2].strip()
                matches.append(_RawMatch(
                    file_path=file_path,
                    line_number=line_num,
                    line_content=content,
                    strategy="ripgrep",
                ))
            except (ValueError, IndexError):
                continue

        return matches

    def _search_with_python(
        self, pattern: str, extensions: list[str]
    ) -> list[_RawMatch]:
        """Pure Python fallback search using pathlib.rglob()."""
        matches: list[_RawMatch] = []
        pattern_lower = pattern.lower()

        for ext in extensions:
            ext_clean = ext if ext.startswith(".") else f".{ext}"
            for filepath in self._repo.rglob(f"*{ext_clean}"):
                if self._is_excluded(filepath):
                    continue
                if not filepath.is_file():
                    continue
                try:
                    content = filepath.read_text(encoding="utf-8", errors="replace")
                    for line_num, line in enumerate(content.splitlines(), start=1):
                        if pattern_lower in line.lower():
                            matches.append(_RawMatch(
                                file_path=filepath,
                                line_number=line_num,
                                line_content=line.strip(),
                                strategy="python_search",
                            ))
                            if len(matches) >= _MAX_CANDIDATES:
                                return matches
                except OSError:
                    continue

        return matches

    # ── Context reader ────────────────────────────────────────────────────────

    def _read_context(
        self, file_path: Path, line_number: int
    ) -> tuple[str, str, str]:
        """
        Read the surrounding context of a match.

        Returns:
            (context_before, matched_line, context_after)
            Each is a string with up to _CONTEXT_LINES lines.
        """
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            total = len(lines)
            idx = line_number - 1  # Convert to 0-indexed

            before_start = max(0, idx - _CONTEXT_LINES)
            after_end = min(total, idx + _CONTEXT_LINES + 1)

            context_before = "\n".join(
                f"{before_start + i + 1}: {l}"
                for i, l in enumerate(lines[before_start:idx])
            )
            matched_line = lines[idx] if 0 <= idx < total else ""
            context_after = "\n".join(
                f"{idx + i + 2}: {l}"
                for i, l in enumerate(lines[idx + 1:after_end])
            )

            return context_before, matched_line, context_after

        except OSError as exc:
            log.warning("source_locator.context_read_failed", file=str(file_path), error=str(exc))
            return "", "", ""

    # ── Confidence computation ────────────────────────────────────────────────

    def _compute_confidence(self, candidates: list[_RawMatch]) -> SourceMatchConfidence:
        """
        Compute the confidence level from the candidate distribution.

        Rules:
        - If there is exactly 1 unique file with the best score ≥ 0.85 → DIRECT_MATCH
        - If 1-2 unique files with best score ≥ 0.50 → LIKELY_MATCH
        - If 3+ unique files → AMBIGUOUS_MATCH
        - If no candidates → NOT_FOUND
        """
        if not candidates:
            return SourceMatchConfidence.NOT_FOUND

        unique_files = len({str(c.file_path) for c in candidates})
        best_score = candidates[0].score  # Already sorted by score desc

        if unique_files == 1 and best_score >= 0.85:
            return SourceMatchConfidence.DIRECT_MATCH
        elif unique_files <= 2 and best_score >= 0.50:
            return SourceMatchConfidence.LIKELY_MATCH
        elif unique_files >= 3:
            return SourceMatchConfidence.AMBIGUOUS_MATCH
        else:
            return SourceMatchConfidence.LIKELY_MATCH

    # ── Helper utilities ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_classes_from_selector(selector: str) -> list[str]:
        """Extract CSS class names from a selector string."""
        # Match .classname but not pseudo-classes like :hover
        return re.findall(r"(?<!:)\.([a-zA-Z][a-zA-Z0-9_-]+)", selector)

    def _is_excluded(self, file_path: Path) -> bool:
        """Return True if the file path should be excluded from search."""
        parts = set(file_path.parts)
        return bool(parts & _EXCLUDED_DIRS)

    def _find_file_by_name(self, filename: str) -> Path | None:
        """Find a file anywhere in the repo by exact name."""
        for match in self._repo.rglob(filename):
            if not self._is_excluded(match) and match.is_file():
                return match
        return None

    def _deduplicate_candidates(self, candidates: list[_RawMatch]) -> list[_RawMatch]:
        """
        Remove duplicate matches (same file + line from different strategies).
        Keeps the highest-scored match for each file:line combination.
        """
        seen: dict[str, _RawMatch] = {}
        for c in candidates:
            key = f"{c.file_path}:{c.line_number}"
            if key not in seen or c.score > seen[key].score:
                seen[key] = c
        return list(seen.values())

    @staticmethod
    def _not_found_result() -> SourceLocation:
        """Return a NOT_FOUND SourceLocation."""
        return SourceLocation(
            file_path="",
            start_line=1,
            end_line=1,
            language="unknown",
            confidence=SourceMatchConfidence.NOT_FOUND,
            matched_text="",
            context_before="",
            context_after="",
            search_strategy="all_strategies_exhausted",
        )

    @staticmethod
    def _check_ripgrep() -> bool:
        """Check whether ripgrep (rg) is installed and accessible."""
        try:
            subprocess.run(
                ["rg", "--version"],
                capture_output=True,
                timeout=5,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
