# AI Accessibility Testing & Autonomous Remediation Platform
## Complete Architecture — Scratch to Production

---

## 1. What We Are Building

A production-grade, enterprise-ready platform with two distinct agents working together:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│              AI ACCESSIBILITY TESTING & REMEDIATION PLATFORM                │
│                                                                             │
│   INPUT: Any web application (HTML / React / Vue / Angular / Django / etc.) │
│                                                                             │
│   OUTPUT: Zero accessibility bugs in production, with a full audit trail    │
└─────────────────────────────────────────────────────────────────────────────┘
```

The system operates in two independent but connected modes:

| Mode | What It Does | Who Uses It |
|------|-------------|-------------|
| **AUDIT MODE** | Scans any live URL, finds WCAG 2.2 AA violations, generates reports | QA Engineers, Managers, Clients |
| **REMEDIATION MODE** | Reads a finding, locates source code, writes a fix, proves it works | Developers, CI/CD pipelines |

---

## 2. Complete System Topology (One Diagram)

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    INPUTS                                                    │
│                                                                                              │
│   CLI: a11y-agent scan --url ...              REST API: POST /api/v1/scan                    │
│   CLI: a11y-agent remediate --finding ...     REST API: POST /api/v1/remediate               │
│   GitHub Actions: on push / pull_request / manual trigger                                    │
└──────────────────────────────────────┬──────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                               AI QA ORCHESTRATOR                                             │
│                              (agent/orchestrator.py)                                         │
│                                                                                              │
│   Routes requests to the correct agent. Controls execution policy. Enforces limits.         │
│   Maintains audit trace. Manages Git branch lifecycle.                                       │
└──────────────────────┬──────────────────────────────────────┬──────────────────────────────┘
                       │                                      │
          ┌────────────▼────────────┐           ┌────────────▼──────────────┐
          │                         │           │                            │
          │   ACCESSIBILITY AUDITOR │           │   REMEDIATION AGENT        │
          │                         │           │                            │
          │  ┌─────────────────┐   │           │  ┌──────────────────────┐  │
          │  │ BrowserController│   │           │  │ RemediationClassifier│  │
          │  │ (Playwright)    │   │           │  │ (SAFE? LIKELY? AI?)  │  │
          │  └────────┬────────┘   │           │  └──────────┬───────────┘  │
          │           │            │           │             │               │
          │  ┌────────▼────────┐   │           │  ┌──────────▼───────────┐  │
          │  │  AxeEngine      │   │           │  │ SourceLocator        │  │
          │  │  (axe-core 4.x) │   │           │  │ HTML/React/Vue/      │  │
          │  └────────┬────────┘   │           │  │ Angular/Django/CSS   │  │
          │           │            │           │  └──────────┬───────────┘  │
          │  ┌────────▼────────┐   │           │             │               │
          │  │ KeyboardTester  │   │           │  ┌──────────▼───────────┐  │
          │  │ FormTester      │   │           │  │ SourceAnalyzer       │  │
          │  └────────┬────────┘   │           │  │ (AST + context)      │  │
          │           │            │           │  └──────────┬───────────┘  │
          │  ┌────────▼────────┐   │           │             │               │
          │  │ AgentPlanner    │   │           │  ┌──────────▼───────────┐  │
          │  │ (Observe/Plan/  │   │           │  │ RemediationPlanner   │  │
          │  │  Act loop)      │   │           │  │ (LLM-assisted plan)  │  │
          │  └────────┬────────┘   │           │  └──────────┬───────────┘  │
          │           │            │           │             │               │
          │  ┌────────▼────────┐   │           │  ┌──────────▼───────────┐  │
          │  │Deduplication    │   │           │  │ PatchGenerator       │  │
          │  │Engine (3-pass)  │   │           │  │ (Unified diff)       │  │
          │  └────────┬────────┘   │           │  └──────────┬───────────┘  │
          │           │            │           │             │               │
          │  ┌────────▼────────┐   │           │  ┌──────────▼───────────┐  │
          │  │ ReasoningEngine │   │           │  │ PatchValidator       │  │
          │  │ (batch AI, RAG) │   │           │  │ (8 independent gates)│  │
          │  └────────┬────────┘   │           │  └──────────┬───────────┘  │
          │           │            │           │             │               │
          │  ┌────────▼────────┐   │           │  ┌──────────▼───────────┐  │
          │  │ ReportGenerator │   │           │  │ GitManager           │  │
          │  │ JSON/HTML/CSV   │   │           │  │ branch/apply/        │  │
          │  └─────────────────┘   │           │  │ rollback/commit/PR   │  │
          │                         │           │  └──────────┬───────────┘  │
          └─────────────────────────┘           │             │               │
                       │                        │  ┌──────────▼───────────┐  │
                       │                        │  │ TestExecutor         │  │
                       │     ┌──────────────────┘  │ pytest/jest/         │  │
                       │     │                     │ playwright           │  │
                       │     │                     │  └──────────┬───────────┘  │
                       │     │                     │             │               │
                       ▼     ▼                     │  ┌──────────▼───────────┐  │
          ┌────────────────────────┐               │  │ VerificationEngine   │  │
          │  ACCESSIBILITY RESCAN  │◄──────────────│  │ before/after compare │  │
          │  (existing Auditor,    │               │  └──────────┬───────────┘  │
          │   same axe-core tags)  │               │             │               │
          └────────────┬───────────┘               │  ┌──────────▼───────────┐  │
                       │                           │  │ VERIFIED / FAILED /  │  │
                       └───────────────────────────┘  │ ROLLBACK / PR        │  │
                                                       └──────────────────────┘  │
                                                       └────────────────────────┘
                                                                  │
                                              ┌───────────────────┴──────────────────┐
                                              │                                      │
                                     ┌────────▼────────┐               ┌────────────▼────────┐
                                     │  AUDIT REPORTS  │               │ REMEDIATION REPORTS  │
                                     │  JSON / HTML    │               │ Audit trail          │
                                     │  CSV / Console  │               │ Diff / PR body       │
                                     └─────────────────┘               └─────────────────────┘
```

---

## 3. The Complete Finding Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                       COMPLETE FINDING LIFECYCLE                                │
└─────────────────────────────────────────────────────────────────────────────────┘

STEP 1 — DETECTION (Auditor)
    Browser renders URL
    AxeEngine runs axe-core → [raw violations + incomplete]
    KeyboardTester simulates Tab key → [focus findings]
    FormTester submits blank forms → [error findings]

STEP 2 — DEDUPLICATION (Auditor)
    3-pass engine:
    Pass 1: Exact component_signature match
    Pass 2: rule_id + normalized selector
    Pass 3: SC + class-based selector + Jaccard similarity
    → Duplicates marked with duplicate_of

STEP 3 — AI ENRICHMENT (Auditor)
    Batch processing (10 findings / API call)
    RAG grounding from 53KB WCAG 2.2 Knowledge Base
    LLM generates: root_cause, html_fix, aria_fix, testing_guidance
    Contradiction detection against other SCs
    status may upgrade: REQUIRES_MANUAL_REVIEW → LIKELY

STEP 4 — REPORTING (Auditor)
    ScanResult.finalize() → compute metrics
    JSON report (machine-readable)
    HTML report (human-readable, with locator boxes)
    CSV report (spreadsheet-based workflows)

STEP 5 — CLASSIFICATION (Remediation Agent)
    Read Finding from report
    Determine RemediationAutomationLevel:
      SAFE_AUTO_FIX         → missing label, lang, title, role on semantic element
      LIKELY_AUTO_FIX       → clear issue, mild ambiguity
      AI_PROPOSED_FIX       → LLM has high confidence
      MANUAL_REVIEW_REQUIRED → semantic ambiguity, alt text meaning, complex behavior
      DO_NOT_AUTO_REMEDIATE → color contrast, visual design, reading order

STEP 6 — SOURCE LOCALIZATION (Remediation Agent)
    Strategy 1: Exact text search (ripgrep all source files)
    Strategy 2: Selector decomposition (.Portal_voteBtn → Portal + voteBtn)
    Strategy 3: Accessible name search (visible button text)
    Strategy 4: Component name inference (class → file)
    Strategy 5: AST analysis (JSX element by role/attributes)
    Strategy 6: Framework heuristics (React/Vue/Angular/Django)
    Result: SourceLocation { file, start_line, end_line, confidence }

STEP 7 — SOURCE ANALYSIS (Remediation Agent)
    Read ±30 lines around the match
    Understand: props, state, event handlers, nearby markup, imports
    Determine problem type: MARKUP | ARIA | KEYBOARD | FOCUS | STYLING | CONTENT

STEP 8 — FIX PLANNING (Remediation Agent)
    LLM generates RemediationPlan:
      { source_file, source_range, root_cause, strategy, risk, tests_required }
    Plan is validated before proceeding

STEP 9 — PATCH GENERATION (Remediation Agent)
    Generate minimal unified diff:
      --- a/src/components/Register.tsx
      +++ b/src/components/Register.tsx
      @@ -74,7 +74,7 @@
      -<button className="register-btn">
      +<button className="register-btn" aria-label="Register">
    Measure: files_changed, lines_added, lines_removed

STEP 10 — PATCH VALIDATION (8 gates) (Remediation Agent)
    Gate 1: Target file exists
    Gate 2: Expected context present in file
    Gate 3: Patch applies cleanly (git apply --check)
    Gate 4: Syntax remains valid (linter/parser)
    Gate 5: No unrelated changes
    Gate 6: No secrets detected
    Gate 7: No invalid ARIA introduced
    Gate 8: No contradiction with other WCAG SCs
    → ALL 8 must pass; otherwise: DO NOT APPLY

STEP 11 — GIT ISOLATION (Remediation Agent)
    git checkout -b a11y-agent/A11Y-XXXXXXXX
    git apply <patch>
    git diff --name-only → must list exactly the target file

STEP 12 — TEST EXECUTION (Remediation Agent)
    Detect available test runners:
      pytest present? → run pytest --tb=short -x
      jest present?   → run jest --passWithNoTests
      playwright present? → run playwright test
    Capture: exit_code, failing_tests, duration, stdout/stderr

STEP 13 — ACCESSIBILITY RE-SCAN (Remediation Agent)
    Call existing ScanOrchestrator (same URL, same axe tags)
    Collect: after_scan findings + metrics

STEP 14 — VERIFICATION (Remediation Agent)
    Compare BEFORE vs AFTER:
      ✓ Original finding_id absent from after-scan
      ✓ No new CONFIRMED findings
      ✓ All tests passed
      → status = VERIFIED

    Any failure:
      → Analyze failure reason
      → Retry (max 3 attempts) with failure context injected into LLM prompt
      → If still failing: ROLLBACK + MANUAL_REVIEW_PACKAGE

STEP 15 — COMMIT + PULL REQUEST (Remediation Agent)
    git commit -m "fix(a11y): A11Y-XXXXXXXX — {description}"
    GitHub PR:
      Title: "fix: A11Y-XXXXXXXX — Button missing accessible name (WCAG 4.1.2)"
      Body:  Finding / Root Cause / Files Changed / Diff / Before / After / Evidence / AI Reasoning
    PR URL stored in RemediationResult.pr_url

STEP 16 — AUDIT TRAIL (Always)
    Every step logged to RemediationResult.audit_trace
    Full evidence preserved: before_scan, after_scan, patch, test_output
    screen_reader_verified = False (always — we don't run AT directly)
```

---

## 4. Multi-Framework Source Localization

The system works on ALL application types using a layered strategy:

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                    MULTI-FRAMEWORK SOURCE LOCALIZATION                         │
└────────────────────────────────────────────────────────────────────────────────┘

DETECTOR (runs first — auto-detects framework)
    Looks for:
    package.json → if "react": React/TSX
    package.json → if "vue": Vue
    package.json → if "@angular/core": Angular
    manage.py / settings.py → Django
    app.py / templates/ → Flask/Jinja2
    *.html only → Static HTML
    *.erb → Rails
    *.blade.php → Laravel

LOCATOR STRATEGIES (run in parallel, ranked by confidence)

  ┌──────────────┬──────────────────────────────────────────────────────────┐
  │ Framework    │ Locator Strategy                                         │
  ├──────────────┼──────────────────────────────────────────────────────────┤
  │ Static HTML  │ grep for class/id/aria attr in *.html files              │
  ├──────────────┼──────────────────────────────────────────────────────────┤
  │ React/TSX    │ Decompose classname → find Component.tsx                  │
  │              │ Parse JSX AST → find element by role/prop                 │
  │              │ Handle CSS-Modules, styled-components, Tailwind           │
  ├──────────────┼──────────────────────────────────────────────────────────┤
  │ Vue          │ grep *.vue SFC <template> blocks for class/aria           │
  │              │ Parse <template> AST for element matching                 │
  ├──────────────┼──────────────────────────────────────────────────────────┤
  │ Angular      │ grep *.component.html for class/aria binding              │
  │              │ Parse component decorator for templateUrl                 │
  ├──────────────┼──────────────────────────────────────────────────────────┤
  │ Django/Flask │ grep templates/*.html for class/aria/tag                  │
  │              │ Understand {% block %} / {{ var }} template syntax        │
  ├──────────────┼──────────────────────────────────────────────────────────┤
  │ CSS/SCSS     │ grep for class name in *.css/*.scss                       │
  │              │ Detect outline:none → keyboard focus violations           │
  └──────────────┴──────────────────────────────────────────────────────────┘

CONFIDENCE SCORING
  DIRECT_MATCH   → 1 file, exact attribute found in 1 location
  LIKELY_MATCH   → class/id found in 1-2 files, strong evidence
  AMBIGUOUS_MATCH→ found in 3+ files → ask LLM to rank, or → MANUAL_REVIEW
  NOT_FOUND      → → MANUAL_REVIEW_PACKAGE immediately

SAFETY RULES
  Never patch a file that doesn't exist
  Never patch a file outside allowed_directories
  Never patch .env, .github/, credentials.*, secrets.*
  Never patch node_modules/ or build/ output directories
```

---

## 5. Complete Data Model Map

```
Finding (existing, extended)
  ├── finding_id: "A11Y-XXXXXXXX"
  ├── url, page_title, timestamp
  ├── element: ElementLocator
  │     ├── selector, xpath, aria_label, role
  │     ├── accessible_name, html, bounding_box
  ├── status: CONFIRMED | LIKELY | REQUIRES_MANUAL_REVIEW | PASS
  ├── wcag: WCAGMapping { success_criterion, level, title, url }
  ├── evidence: list[EvidenceItem]
  │     ├── screenshot (base64, shared ref)
  │     ├── dom_snippet
  │     ├── ax_tree
  │     └── computed_style
  └── remediation: RemediationGuidance { html_fix, aria_fix, css_fix }

RemediationResult (new)
  ├── remediation_id: "REM-XXXXXXXX"
  ├── finding_id: "A11Y-XXXXXXXX"
  ├── status: VERIFIED | FAILED | ROLLED_BACK | MANUAL_REVIEW | REJECTED
  ├── automation_level: SAFE_AUTO_FIX | LIKELY_AUTO_FIX | AI_PROPOSED_FIX | ...
  ├── source: SourceLocation
  │     ├── file_path: "src/components/Register.tsx"
  │     ├── start_line: 74, end_line: 76
  │     ├── language: "tsx"
  │     ├── confidence: DIRECT_MATCH
  │     └── context_before, matched_text, context_after
  ├── patch: GeneratedPatch
  │     ├── unified_diff: "--- a/... +++ b/..."
  │     ├── lines_added: 1, lines_removed: 1
  │     └── patch_hash: "sha256:..."
  ├── validation: PatchValidationResult
  │     ├── is_valid: true
  │     └── [all 8 gate results]
  ├── git_branch: "a11y-agent/A11Y-XXXXXXXX"
  ├── git_commit: "abc123def"
  ├── pr_url: "https://github.com/org/repo/pull/42"
  ├── test_results: { exit_code, failing_tests, duration }
  ├── before_scan: { confirmed: 5, likely: 0, ... }
  ├── after_scan:  { confirmed: 4, likely: 0, ... }
  ├── regressions_detected: []
  ├── original_issue_resolved: true
  ├── rollback_performed: false
  ├── attempts: 1
  ├── audit_trace: [list of every step, tool, input, output]
  └── screen_reader_verified: false  ← ALWAYS FALSE (we don't run AT)
```

---

## 6. Technology Stack

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TECHNOLOGY STACK                                  │
└─────────────────────────────────────────────────────────────────────────────┘

RUNTIME
  Python 3.11+

BROWSER AUTOMATION
  Playwright (async API) — Chromium / Firefox / WebKit

ACCESSIBILITY ENGINE
  axe-core 4.9.x (injected via Playwright evaluate())
  Custom keyboard tester (Tab simulation + JS computed styles)
  Custom form tester (form submission + error announcement)

AI / LLM
  Groq LPU API (primary — openai/gpt-oss-120b, 500 tokens/sec)
  OpenAI API (fallback)
  Google Gemini (fallback)
  Ollama (local, offline mode)
  Multi-key round-robin failover (existing, working)

WCAG KNOWLEDGE
  In-memory BM25 knowledge base (53KB, all WCAG 2.2 SC)
  ARIA Authoring Practices Guide patterns
  Contradiction pattern detection

SOURCE LOCALIZATION
  ripgrep (rg) — fast multi-file text search via subprocess
  Python ast module — Python source AST parsing
  tree-sitter (planned Phase 4+) — JS/TSX/Vue AST parsing

PATCH GENERATION & GIT
  unidiff — Python unified diff parsing/generation
  gitpython — Git operations (branch, apply, commit, rollback)
  GitHub REST API / gh CLI — Pull Request creation

VALIDATION
  Pydantic v2 — ALL data models, strict validation
  pydantic-settings — environment variable management
  ruff — linter (for validating patched Python/JS syntax)

WEB FRAMEWORK
  FastAPI — REST API server
  uvicorn — ASGI runner

CLI
  Typer — Command-line interface
  Rich — Console output, tables, progress bars

LOGGING
  structlog — structured JSON logging

TESTING
  pytest + pytest-asyncio — unit + integration tests
  pytest-playwright — E2E browser tests

CONTAINERIZATION
  Docker — single-container deployment
  Docker Compose — multi-container (API + worker)

CI/CD
  GitHub Actions — automated scan + remediation pipeline
```

---

## 7. Complete File System After All Phases

```
src/accessibility_agent/
│
├── config.py                      [EXTEND] — add remediation settings block
├── logging_config.py              [KEEP]
├── cli.py                         [EXTEND] — add `remediate` subcommand
├── __init__.py                    [KEEP]
│
├── accessibility/
│   ├── browser.py                 [KEEP + minor fix] — wait_for_load_state added ✓
│   ├── axe_engine.py              [KEEP]
│   ├── deduplication.py           [KEEP]
│   ├── form_tester.py             [KEEP]
│   └── keyboard_tester.py        [KEEP]
│
├── agent/
│   ├── orchestrator.py            [KEEP] — Auditor orchestrator
│   ├── planner.py                 [KEEP] — Auditor Observe/Plan/Act
│   └── job_manager.py             [EXTEND] — add remediation job tracking
│
├── ai/
│   ├── llm_client.py              [KEEP] — reused by Remediation Agent
│   ├── reasoning_engine.py        [KEEP]
│   └── prompts.py                 [EXTEND] — add remediation-specific prompts
│
├── wcag/
│   ├── schemas.py                 [EXTEND] — add RemediationStatus, SCRelationship enums
│   ├── criteria.py                [KEEP]
│   ├── knowledge_base.py          [KEEP]
│   ├── mapper.py                  [KEEP]
│   └── rag_engine.py              [REFACTOR] — SCRelationship contradiction model
│
├── evidence/
│   └── collector.py               [KEEP]
│
├── reporting/
│   └── generator.py               [EXTEND] — add remediation audit section to HTML report
│
├── api/
│   ├── main.py                    [KEEP]
│   ├── routes.py                  [EXTEND] — POST /api/v1/remediate, GET /api/v1/remediations/{id}
│   └── server.py                  [KEEP]
│
└── remediation/                   [NEW PACKAGE — 13 files]
    ├── __init__.py
    ├── agent.py                   # RemediationAgent — main entry point, state machine driver
    ├── state.py                   # RemediationStateMachine — enforces valid transitions
    ├── classifier.py              # Determines automation level (deterministic rules first)
    ├── locator.py                 # SourceLocator — multi-framework DOM-to-source mapping
    ├── analyzer.py                # SourceAnalyzer — reads context, understands component
    ├── planner.py                 # RemediationPlanner — LLM-assisted fix plan
    ├── patcher.py                 # PatchGenerator — produces unified diffs
    ├── validator.py               # PatchValidator — 8-gate validation
    ├── git_manager.py             # GitManager — branch / apply / rollback / commit / PR
    ├── test_runner.py             # TestExecutor — detects + runs pytest/jest/playwright
    ├── verifier.py                # VerificationEngine — before/after comparison
    ├── policy.py                  # RemediationPolicy — configurable thresholds
    ├── schemas.py                 # SourceLocation, GeneratedPatch, RemediationResult etc.
    └── tools.py                   # All controlled tool wrappers (allowlisted, logged)

tests/
├── unit/
│   ├── test_schemas.py            [KEEP]
│   ├── test_deduplication.py      [KEEP]
│   ├── test_wcag_mapper.py        [KEEP]
│   ├── test_agent_planner.py      [KEEP]
│   ├── test_remediation_classifier.py   [NEW]
│   ├── test_source_locator.py           [NEW]
│   ├── test_patch_generator.py          [NEW]
│   ├── test_patch_validator.py          [NEW]
│   └── test_verification_engine.py      [NEW]
│
├── integration/
│   └── test_remediation_agent.py        [NEW] — full flow against fixture repos
│
├── benchmark/
│   └── remediation_benchmark.py         [NEW] — evaluation framework
│
└── fixtures/                            [NEW]
    ├── html_missing_label/              # Static HTML with known bugs
    ├── react_missing_aria/              # Minimal React app fixture
    ├── vue_missing_lang/                # Minimal Vue fixture
    └── django_missing_title/            # Minimal Django template fixture
```

---

## 8. Remediation Automation Level Rules

```
SAFE_AUTO_FIX (deterministic — no human needed)
  → Missing html lang attribute:              <html> → <html lang="en">
  → Missing document title:                  add <title>Page Name</title>
  → Button/input with no accessible name:    add aria-label="..."
  → Image with no alt:                       add alt="" (decorative) or alt="desc"
  → Form input with no label:               add <label for="...">
  → Missing role on custom widget:           add role="button" + tabindex="0"
  → landmark missing:                        wrap in <main> / <nav> / <header>

LIKELY_AUTO_FIX (AI-assisted, high confidence)
  → Incorrect heading hierarchy:             restructure heading levels
  → Link with generic text "click here":    add aria-label with context
  → Missing focus style in CSS:              add :focus-visible { outline: ... }
  → Positive tabindex values:               replace tabindex="2" → tabindex="0"
  → aria-hidden on interactive element:     remove aria-hidden="true"

AI_PROPOSED_FIX (AI generates, human reviews in PR)
  → Complex ARIA widget reconstruction
  → Dynamic content with live region needs
  → Focus management in SPA navigation
  → Error identification with custom patterns

MANUAL_REVIEW_REQUIRED (always)
  → Color contrast (requires visual judgment + design input)
  → Meaningful alt text (requires domain knowledge)
  → Reading order (requires UX review)
  → Screen reader speech output (requires AT testing)
  → Complex semantic relationships

DO_NOT_AUTO_REMEDIATE (never touch automatically)
  → Authentication flows
  → Payment forms
  → Medical data entry
  → Legal document content
```

---

## 9. Git Workflow

```
main (protected)
  │
  ├── a11y-agent/A11Y-AABB1122  ← Remediation Agent creates this
  │     │
  │     ├── apply patch
  │     ├── run tests
  │     ├── re-scan accessibility
  │     ├── VERIFIED → commit + push
  │     └── create PR → GitHub review → merge to main
  │
  └── a11y-agent/A11Y-CCDD3344  ← Another finding, another branch
```

**PR Body (auto-generated)**
```markdown
## ♿ Accessibility Fix — A11Y-AABB1122

**WCAG Criterion**: 4.1.2 — Name, Role, Value (Level A)
**Finding**: Button does not have an accessible name.

### Root Cause
The `.register-btn` button element had no visible text content, no aria-label,
and no aria-labelledby attribute. Screen readers announced it as "button" only,
with no context for the user.

### Files Changed
- `src/components/Register.tsx` (+1 line, -1 line)

### Patch Applied
```diff
-<button className="register-btn" onClick={handleRegister}>
+<button className="register-btn" onClick={handleRegister} aria-label="Register">
```

### Verification
| Check | Result |
|-------|--------|
| Patch applies cleanly | ✅ |
| Syntax valid | ✅ |
| No unrelated changes | ✅ |
| Unit tests | ✅ PASSED |
| Accessibility re-scan | ✅ 0 new violations |
| Original issue resolved | ✅ VERIFIED |
| New regressions | ✅ None |

### Before / After
| Metric | Before | After |
|--------|--------|-------|
| Confirmed Findings | 5 | 4 |
| WCAG 4.1.2 Violations | 1 | 0 |

**Agent Confidence**: 0.97
**Remediation Attempts**: 1
**Screen Reader Verified**: ❌ Not directly (requires human AT testing)

_Generated by AI Accessibility Remediation Agent_
```

---

## 10. REST API Endpoints

```
EXISTING (keep)
  GET  /                              → health check
  POST /api/v1/scan                   → start async scan
  GET  /api/v1/scans/{run_id}/status  → scan status
  GET  /api/v1/scans/{run_id}/report  → scan results

NEW (add)
  POST /api/v1/remediate              → start remediation for a finding
       Body: { finding_id, scan_run_id, repo_path, policy }
       Response: { remediation_id, status: "queued" }

  GET  /api/v1/remediations/{id}      → remediation status + result
       Response: RemediationResult

  GET  /api/v1/remediations/{id}/diff → view the generated patch
       Response: { unified_diff, files_changed, lines_added }

  GET  /api/v1/remediations/{id}/trace → full audit trail
       Response: { events: [...] }

  POST /api/v1/remediations/{id}/rollback → manually trigger rollback
       Response: { rolled_back: true }
```

---

## 11. Complete CLI Commands

```bash
# ─── AUDITOR COMMANDS (existing, unchanged) ───────────────────────────────────

# Basic scan
a11y-agent scan --url https://example.com

# Full agentic scan with AI reasoning
a11y-agent scan --url https://example.com --mode automated --agentic

# Custom browser + viewport
a11y-agent scan --url https://example.com --browser firefox --no-headless

# Start the API server
a11y-agent serve --host 0.0.0.0 --port 8000

# ─── REMEDIATION COMMANDS (new) ───────────────────────────────────────────────

# Dry-run: inspect finding, locate source, propose fix — do NOT touch anything
a11y-agent remediate \
  --finding A11Y-XXXXXXXX \
  --finding-file ./reports/RUN-XXXX_report.json \
  --repo ./my-frontend-app \
  --dry-run

# Full autonomous remediation with PR creation
a11y-agent remediate \
  --finding A11Y-XXXXXXXX \
  --finding-file ./reports/RUN-XXXX_report.json \
  --repo ./my-frontend-app \
  --agentic \
  --auto-repair \
  --max-attempts 3 \
  --create-branch \
  --verify \
  --create-pr

# Remediate all confirmed findings from a scan report
a11y-agent remediate-all \
  --scan-report ./reports/RUN-XXXX_report.json \
  --repo ./my-frontend-app \
  --automation-level safe   # only SAFE_AUTO_FIX findings
  --create-pr
```

---

## 12. GitHub Actions Pipeline

```yaml
# .github/workflows/accessibility-audit.yml (in THIS repo)
# Runs on manual trigger against any URL

# .github/workflows/a11y-gate.yml (copy to TARGET frontend repo)
# Blocks PR merges on accessibility violations

name: AI Accessibility Gate

on:
  pull_request:
    branches: [main]
  workflow_dispatch:
    inputs:
      target_url:
        description: "URL to scan"
        required: false

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Scan for accessibility violations
        env:
          A11Y_GROQ_API_KEYS: ${{ secrets.GROQ_API_KEY }}
        run: |
          # Scan the PR preview URL
          python -m accessibility_agent.cli scan \
            --url "$TARGET_URL" \
            --mode automated \
            --agentic
          
      - name: Auto-remediate SAFE issues
        if: failure()  # only if scan found bugs
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          A11Y_GROQ_API_KEYS: ${{ secrets.GROQ_API_KEY }}
        run: |
          python -m accessibility_agent.cli remediate-all \
            --scan-report ./reports/latest_report.json \
            --repo . \
            --automation-level safe \
            --create-pr
            
      - name: Upload accessibility report
        uses: actions/upload-artifact@v4
        with:
          name: a11y-report
          path: reports/*.html
```

---

## 13. Deployment Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   DEPLOYMENT OPTIONS                             │
└─────────────────────────────────────────────────────────────────┘

OPTION 1: LOCAL CLI (Developer Machine)
  python -m accessibility_agent.cli scan --url ...
  python -m accessibility_agent.cli remediate --finding ...
  → Reports in ./reports/
  → Patches applied to local repo

OPTION 2: DOCKER (Single Container)
  docker build -t a11y-agent .
  docker run -p 8000:8000 \
    -v ./reports:/app/reports \
    -v ./my-app:/app/target-repo \
    -e A11Y_GROQ_API_KEYS=... \
    a11y-agent
  → Starts REST API on :8000
  → Scans + remediates via API

OPTION 3: DOCKER COMPOSE (API + Worker)
  services:
    api:
      image: a11y-agent
      ports: ["8000:8000"]
      command: a11y-agent serve
    worker:
      image: a11y-agent
      command: python -m accessibility_agent.worker
      # processes scan + remediation jobs from queue

OPTION 4: GITHUB ACTIONS (CI/CD Gate)
  Runs on every PR push
  Blocks merge on critical violations
  Auto-creates fix PRs for SAFE_AUTO_FIX issues

OPTION 5: CLOUD (AWS / GCP / Azure)
  Deploy Docker image to ECS / Cloud Run / Container Apps
  REST API accessible at https://a11y.yourcompany.com
  Integrate with Jira / Linear / GitHub Issues
  Dashboard at https://a11y.yourcompany.com/docs

INFRASTRUCTURE REQUIREMENTS
  CPU: 2 vCPU minimum (Playwright is CPU-intensive)
  RAM: 2 GB minimum (Chromium + Python process)
  Storage: 10 GB (reports + evidence accumulate)
  Network: Outbound access to Groq API + target URLs
  Secrets: GROQ_API_KEY, GITHUB_TOKEN (for PR creation)
```

---

## 14. Phased Implementation Roadmap

```
PHASE 0 — Architecture Analysis (COMPLETE ✓)
  Repository deep-dive, component classification, data models, plan

PHASE 1 — Source Localization [~1 week]
  SourceLocator: ripgrep-based multi-framework file search
  SourceLocation schema
  Tests: 12 fixture cases (HTML, TSX, Vue, Django)
  Deliverable: Given any Finding → correct source file + lines

PHASE 2 — Source Analysis + Classification [~1 week]
  SourceAnalyzer: read ±30 lines, understand component context
  RemediationClassifier: deterministic rules + LLM fallback
  RemediationPlanner: structured fix plan
  Deliverable: Given source file → typed RemediationPlan

PHASE 3 — Patch Generation [~1 week]
  PatchGenerator: minimal unified diff output
  GeneratedPatch schema
  git apply --check validation
  Deliverable: Given RemediationPlan → valid unified diff

PHASE 4 — Patch Validation [~3 days]
  PatchValidator: all 8 gates
  RAG contradiction refactor → SCRelationship model
  Deliverable: is_valid=True only when all 8 gates pass

PHASE 5 — Git Isolation [~3 days]
  GitManager: create_branch, apply_patch, rollback
  Branch naming: a11y-agent/A11Y-XXXXXXXX
  Deliverable: Patch applied in isolated branch; main untouched

PHASE 6 — Test Execution [~3 days]
  TestExecutor: auto-detect pytest/jest/playwright
  Configurable: warn vs block on failure
  Deliverable: Test results captured with exit code + failing test names

PHASE 7 — Accessibility Re-scan [~2 days]
  VerificationEngine: call existing ScanOrchestrator
  Before/after ScanResult comparison
  Deliverable: Quantified before/after metrics

PHASE 8 — Verification [~2 days]
  VerificationEngine.verify(): 3-condition VERIFIED gate
  Regression detection
  Deliverable: VERIFIED only when evidence proves it

PHASE 9 — State Machine + Repair Loop [~3 days]
  RemediationStateMachine: enforced state transitions
  Retry loop: max 3 attempts with failure context
  Deliverable: Full flow from RECEIVED → VERIFIED | ROLLBACK

PHASE 10 — Rollback [~2 days]
  GitManager.rollback(): restore branch, preserve evidence
  Manual review package generator
  Deliverable: Safe rollback with full evidence preservation

PHASE 11 — Commit + PR Creation [~3 days]
  GitManager.commit_and_push()
  GitHub REST API PR creation with auto-generated body
  Deliverable: PR created with full evidence in body

PHASE 12 — CLI + API Integration [~3 days]
  `remediate` + `remediate-all` CLI subcommands
  POST /api/v1/remediate REST endpoint
  Updated HTML report with remediation section
  Deliverable: End-to-end usable via CLI and API

PHASE 13 — Evaluation Framework [~3 days]
  20 benchmark cases across frameworks
  Automated metrics: fix rate, regression rate, latency, tokens
  Deliverable: Reproducible benchmark score

PHASE 14 — Production Hardening [~1 week]
  Timeouts per phase, retry policies, structured metrics
  Full integration test suite
  Docker Compose deployment
  CI/CD pipeline update
  Documentation
  Deliverable: Production-ready v1.0 release

TOTAL ESTIMATED: ~8-10 weeks of focused development
```

---

## 15. What the Final Product Looks Like

```
ENGINEER:
  a11y-agent scan --url https://myapp.com --agentic

  → Scan complete: 3 confirmed findings

ENGINEER:
  a11y-agent remediate-all \
    --scan-report ./reports/latest.json \
    --repo ./myapp \
    --automation-level safe \
    --verify \
    --create-pr

  → Remediating A11Y-AA11... SAFE_AUTO_FIX
    ✓ Source located: src/components/Nav.tsx:42
    ✓ Patch generated: +1 line (aria-label="Main navigation")
    ✓ Patch validated: all 8 gates passed
    ✓ Branch created: a11y-agent/A11Y-AA11
    ✓ Patch applied
    ✓ Tests: PASSED
    ✓ Re-scan: 0 violations (was 1)
    ✓ No regressions
    ✓ STATUS: VERIFIED
    ✓ PR created: https://github.com/org/myapp/pull/88

  → Remediating A11Y-BB22... MANUAL_REVIEW_REQUIRED
    ⚠ Source: src/components/Hero.tsx:18 — AMBIGUOUS_MATCH (3 files)
    ⚠ Manual review package saved: ./reports/manual/A11Y-BB22.json
    → Skipped (requires human review)

  Summary:
    2 findings processed
    1 VERIFIED (auto-fixed + PR created)
    1 MANUAL_REVIEW (package generated)
    0 ROLLBACKS
```

---

*This document represents the complete architecture from scratch to production deployment.*
*Phase 0 is complete. Phase 1 begins upon approval.*
