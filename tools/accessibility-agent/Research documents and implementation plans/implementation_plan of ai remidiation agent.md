# Autonomous AI Accessibility Remediation Agent
## Phase 0 — Repository Architecture Analysis & Implementation Plan

> **STOP POINT**: This document is the deliverable for Phase 0.
> No implementation code is written until this plan is reviewed and approved.

---

## 1. Executive Summary

The existing system is a **well-structured, production-quality Accessibility Auditor**. Its
architecture already enforces: schema-validated findings, multi-level deduplication,
batch AI reasoning, RAG-grounded WCAG knowledge, structured logging, and clean separation
of concerns. The system does NOT yet have the ability to touch source code.

The mission is to evolve it into a **closed-loop, evidence-driven Remediation Agent**
that sits alongside the existing Auditor without touching it.

---

## 2. Existing Repository Architecture

### 2.1 Package Map

```
src/accessibility_agent/
├── config.py                  # Pydantic-settings config, env-var loading, secret management
├── logging_config.py          # structlog structured logger
├── cli.py                     # Typer CLI: scan / serve / version commands
│
├── accessibility/
│   ├── browser.py             # Playwright BrowserController (context manager)
│   ├── axe_engine.py          # axe-core injection, rule execution, result normalisation
│   ├── deduplication.py       # 3-pass Finding deduplication engine (deterministic)
│   ├── form_tester.py         # Form interaction + error-announcement testing
│   └── keyboard_tester.py     # Tab-order, focus-visibility, modal-trap testing
│
├── agent/
│   ├── orchestrator.py        # ScanOrchestrator — full scan pipeline coordinator
│   ├── planner.py             # AgentPlanner — Observe/Plan/Act LLM loop
│   └── job_manager.py         # AsyncJobManager — background scan job tracking
│
├── ai/
│   ├── llm_client.py          # BaseLLMClient + Gemini/OpenAI/Ollama/Groq implementations
│   ├── reasoning_engine.py    # ReasoningEngine — batch AI enrichment of findings
│   └── prompts.py             # All prompt templates (system, batch, planner, test-plan)
│
├── wcag/
│   ├── schemas.py             # ALL Pydantic models: Finding, ScanResult, EvidenceItem, etc.
│   ├── criteria.py            # WCAG 2.2 criteria reference data
│   ├── knowledge_base.py      # In-memory WCAG KB + ARIA patterns (53 KB)
│   ├── mapper.py              # Rule-ID → WCAG SC mapper (axe rule → SC number)
│   └── rag_engine.py          # RAGEngine: BM25 search, context building, contradiction detection
│
├── evidence/
│   └── collector.py           # EvidenceCollector: screenshots, DOM, AX-tree snapshots
│
├── reporting/
│   └── generator.py           # ReportGenerator: JSON, HTML, CSV report generation
│
└── api/
    ├── main.py                # FastAPI app factory
    ├── routes.py              # /api/v1/scan, /api/v1/scans/{id}/report endpoints
    └── server.py              # uvicorn runner
```

### 2.2 Existing Data Flow

```
URL
 │
 ▼
ScanOrchestrator.run()
 │
 ├─ BrowserController → navigate, wait_for_load_state
 ├─ EvidenceCollector → capture_screenshot, get_dom, get_ax_tree
 ├─ AxeEngine        → inject + run axe-core → [Finding]
 ├─ KeyboardTester   → Tab simulation → [Finding]
 ├─ FormTester       → submit + error test → [Finding]
 │
 ├─ AgentPlanner (if --agentic)
 │   ├─ generate_test_plan() → scan_plan stored in ScanResult
 │   └─ get_next_interactions() → click elements → re-scan (5 iterations max)
 │
 ├─ DeduplicationEngine.deduplicate([Finding])
 │   └─ Marks duplicate_of on secondary findings
 │
 ├─ ReasoningEngine.enrich_findings([Finding])
 │   ├─ RAGEngine.build_context_for_finding() → WCAG grounding text
 │   ├─ LLMClient.generate(batch_prompt) → JSON array
 │   └─ Apply: root_cause, html_fix, aria_fix, contradiction_warnings
 │
 ├─ ScanResult.finalize() → compute metrics
 └─ ReportGenerator → JSON + HTML + CSV reports
```

### 2.3 Existing Finding Lifecycle

```
CREATED (by axe/keyboard/form tester)
  │  status=CONFIRMED | REQUIRES_MANUAL_REVIEW
  ▼
DEDUPLICATED (duplicate_of set on duplicates)
  ▼
AI ENRICHED (root_cause, html_fix, aria_fix, contradiction_warnings added)
  │  detection_method → AI_ASSISTED
  │  status may change: REQUIRES_MANUAL_REVIEW → LIKELY | PASS
  ▼
REPORTED (in JSON, HTML, CSV)
```

### 2.4 Existing AI Lifecycle

```
ReasoningEngine.enrich_findings()
  │
  ├─ Splits: confirmed | manual_review
  ├─ Batches by settings.llm_batch_size (default 10)
  │
  └─ Per batch:
      ├─ RAGEngine.build_context_for_finding() [first finding's SC]
      ├─ build_batch_reasoning_prompt(batch, is_manual_review)
      ├─ LLMClient.generate(prompt)
      ├─ _extract_json_array(response.text)
      └─ _apply_result(finding, data)  ← mutates Finding in-place
```

### 2.5 Existing Remediation Lifecycle

The system currently has **NO remediation lifecycle**. It stops at:
- `RemediationGuidance` — a schema storing `html_fix`, `aria_fix`, `css_fix`, `javascript_fix`
- `contradiction_warnings` — regex-based anti-pattern check against the proposed LLM fix

There is no: source localization, patch generation, patch validation, git isolation,
test execution, re-scan, or verification.

---

## 3. Component Classification

| Component | Action | Reason |
|---|---|---|
| `wcag/schemas.py` (Finding, ScanResult, EvidenceItem etc.) | **EXTEND** | Add RemediationResult, RemediationStatus, SourceLocation schemas |
| `wcag/schemas.py` (RemediationGuidance) | **EXTEND** | Add patch, diff, source_file fields |
| `ai/llm_client.py` | **KEEP** | Fully reusable by Remediation Agent via `create_llm_client()` |
| `ai/reasoning_engine.py` | **KEEP** | Auditor only; do not touch |
| `wcag/rag_engine.py` (`check_fix_for_contradictions`) | **REFACTOR** | Contradiction model must be explicit SCRelationship |
| `wcag/rag_engine.py` (search, build_context) | **KEEP** | Reusable by Remediation Agent |
| `accessibility/browser.py` | **KEEP** | Reusable for re-scan |
| `accessibility/axe_engine.py` | **KEEP** | Reusable for re-scan |
| `accessibility/deduplication.py` | **KEEP** | Reusable for re-scan |
| `agent/orchestrator.py` | **KEEP** | Auditor; Remediation calls it for re-scan |
| `agent/planner.py` | **KEEP** | Auditor only |
| `evidence/collector.py` | **KEEP** | Reusable for before/after evidence |
| `config.py` | **EXTEND** | Add remediation-specific settings |
| `cli.py` | **EXTEND** | Add `remediate` subcommand |
| `reporting/generator.py` | **EXTEND** | Add remediation report section |
| `api/routes.py` | **EXTEND** | Add `/api/v1/remediate` endpoint |
| `agent/job_manager.py` | **EXTEND** | Track remediation jobs |

---

## 4. Existing Weaknesses Relevant to Remediation

1. **RAG Contradiction Logic is too broad**: `check_fix_for_contradictions` is pattern-based but does not model SC *relationships*. Two different SCs are not automatically in conflict — must be refactored.
2. **No source-code awareness**: The system knows DOM selectors and HTML snippets, but has no knowledge of the application's actual source files. DOM ≠ Source.
3. **RemediationGuidance is suggestion-only**: `html_fix` is a text suggestion with no validation, no patch format, no file path, and no applicability check.
4. **No Git abstraction**: No git operations of any kind.
5. **No test execution**: No integration with pytest, jest, or any external test runner.
6. **All LLM calls are unstructured text responses**: The prompts request JSON but rely on regex extraction. Structured output (function calling) would be safer for remediation.

---

## 5. New System Architecture

```
                     AI QA ORCHESTRATOR
                            │
          ┌─────────────────┴─────────────────┐
          │                                   │
          ▼                                   ▼
  ACCESSIBILITY AUDITOR              REMEDIATION AGENT
  (Existing: orchestrator.py)        (New: remediation/)
          │                                   │
   Detect + Evidence             ┌────────────┴──────────────┐
   WCAG Mapping                  │                           │
   Deduplication                 ▼                           ▼
   AI Enrichment         SOURCE LOCATOR              POLICY ENFORCER
          │              (locator.py)               (policy.py)
          │                    │
          │              CODE ANALYZER
          │              (analyzer.py)
          │                    │
          │              FIX PLANNER
          │              (planner.py)
          │                    │
          │              PATCH GENERATOR
          │              (patcher.py)
          │                    │
          │              PATCH VALIDATOR
          │              (validator.py)
          │                    │
          │              GIT MANAGER
          │              (git_manager.py)
          │                    │
          │              TEST EXECUTOR
          │              (test_runner.py)
          │                    │
          └────────────────────┤
                               ▼
                        ACCESSIBILITY RESCAN
                        (calls existing ScanOrchestrator)
                               │
                               ▼
                       VERIFICATION ENGINE
                       (verifier.py)
                               │
                    ┌──────────┴──────────┐
                    ▼                     ▼
                VERIFIED              FAILED
                    │                     │
                    ▼             ┌───────┴────────┐
               COMMIT/PR       RETRY          ROLLBACK
                              (max 3)        MANUAL_REVIEW
```

---

## 6. Remediation State Machine

```
RECEIVED → VALIDATING_INPUT
  ├── INVALID → REJECTED
  └── VALID → CLASSIFYING
        ├── DO_NOT_AUTO_REMEDIATE → MANUAL_REVIEW_PACKAGE
        ├── MANUAL_REVIEW_REQUIRED → MANUAL_REVIEW_PACKAGE
        └── (SAFE | LIKELY | AI_PROPOSED) → LOCATING_SOURCE
              ├── SOURCE_NOT_FOUND → MANUAL_REVIEW_PACKAGE
              ├── AMBIGUOUS_SOURCE → MANUAL_REVIEW_PACKAGE
              └── SOURCE_LOCATED → ANALYZING_SOURCE → PLANNING_FIX
                    └── GENERATING_PATCH
                          ├── PATCH_INVALID → retry or MANUAL_REVIEW
                          └── PATCH_VALID → CREATING_BRANCH → APPLYING_PATCH
                                ├── APPLY_FAILED → ROLLBACK
                                └── APPLIED → TESTING
                                      ├── TEST_FAILED → ROLLBACK | retry
                                      └── TESTS_PASSED → RESCANNING → VERIFYING
                                            ├── VERIFIED ✓
                                            ├── REGRESSION → ROLLBACK | retry
                                            └── ISSUE_REMAINS → retry | ROLLBACK
```

---

## 7. Refactored Contradiction Model

> **Current problem**: `check_fix_for_contradictions` flags any pattern matching a different SC as a contradiction. Two different Success Criteria are not automatically in conflict.

### New Relationship Types

```python
class SCRelationship(str, Enum):
    UNRELATED = "unrelated"
    RELATED = "related"
    COMPATIBLE = "compatible"
    POTENTIALLY_INTERACTING = "potentially_interacting"
    ACTUAL_CONFLICT = "actual_conflict"
    UNKNOWN = "unknown"
```

A contradiction is only `ACTUAL_CONFLICT` when:
1. The proposed fix contains a pattern known to **cause a failure** in a different SC.
2. The `on_selector` constraint matches the specific element type.
3. The pattern does not serve as an intentional fix for the SC being addressed.

---

## 8. New Data Models

### SourceLocation
```python
class SourceMatchConfidence(str, Enum):
    DIRECT_MATCH = "direct_match"   # Exact selector/attribute in one file
    LIKELY_MATCH = "likely_match"   # Strong indicator, 1-2 files
    AMBIGUOUS_MATCH = "ambiguous_match"  # Multiple possible locations
    NOT_FOUND = "not_found"

class SourceLocation(BaseModel):
    file_path: str              # Relative to repo root
    start_line: int
    end_line: int
    language: str               # tsx | html | vue | py | css
    confidence: SourceMatchConfidence
    matched_text: str
    context_before: str
    context_after: str
    search_strategy: str        # Which locator strategy found it
```

### RemediationAutomationLevel
```python
class RemediationAutomationLevel(str, Enum):
    SAFE_AUTO_FIX = "safe_auto_fix"           # Missing label, missing lang, etc.
    LIKELY_AUTO_FIX = "likely_auto_fix"       # Clear issue, minor ambiguity
    AI_PROPOSED_FIX = "ai_proposed_fix"       # AI has high confidence
    MANUAL_REVIEW_REQUIRED = "manual_review_required"   # Semantic ambiguity
    DO_NOT_AUTO_REMEDIATE = "do_not_auto_remediate"     # Too risky
```

### GeneratedPatch
```python
class GeneratedPatch(BaseModel):
    patch_id: str
    finding_id: str
    target_file: str
    unified_diff: str           # Standard --- a/ +++ b/ format
    lines_added: int
    lines_removed: int
    files_changed: int = 1
    patch_hash: str             # SHA256 of the diff
    is_minimal: bool
    unrelated_changes_detected: bool
```

### RemediationResult (final output schema)
```python
class RemediationStatus(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    MANUAL_REVIEW = "manual_review"
    REJECTED = "rejected"

class RemediationResult(BaseModel):
    remediation_id: str
    finding_id: str
    status: RemediationStatus
    automation_level: RemediationAutomationLevel
    confidence: float
    source: SourceLocation | None
    wcag: WCAGMapping
    root_cause: str
    fix_strategy: str
    patch: GeneratedPatch | None
    git_branch: str | None
    git_commit: str | None
    pr_url: str | None
    test_results: dict | None
    before_scan: dict | None
    after_scan: dict | None
    regressions_detected: list[str]
    original_issue_resolved: bool
    rollback_performed: bool
    rollback_reason: str
    manual_review_package: dict | None
    attempts: int
    audit_trace: list[dict]       # Full ordered event log
    screen_reader_verified: bool = False  # ALWAYS False — we don't run AT
    created_at: datetime
    completed_at: datetime | None
```

---

## 9. Tool Architecture

Each tool is a typed, logged, validated Python function. The LLM cannot call tools
directly — the state machine calls them in a controlled sequence.

```python
# Source tools (read-only)
search_repository(repo_path, query, extensions) -> list[SourceLocation]
read_file(repo_path, file_path, start_line, end_line) -> str
search_symbol(repo_path, symbol_name) -> list[SourceLocation]
inspect_ast(repo_path, file_path) -> dict

# Git tools (branch-isolated)
create_branch(repo_path, branch_name) -> bool
apply_patch(repo_path, patch: GeneratedPatch) -> bool
rollback_patch(repo_path, branch_name) -> bool
commit_changes(repo_path, branch, message) -> str
create_pull_request(repo_url, branch, title, body) -> str

# Validation tools
validate_patch(repo_path, patch) -> PatchValidationResult
run_linter(repo_path, file_path) -> dict
run_typecheck(repo_path) -> dict
run_unit_tests(repo_path, pattern) -> dict
run_e2e_tests(repo_path, pattern) -> dict

# Accessibility tools (reuse existing)
run_accessibility_scan(url, mode) -> ScanResult
compare_accessibility_results(before, after, finding_id) -> dict
```

---

## 10. New Files to Create

```
src/accessibility_agent/remediation/
├── __init__.py
├── agent.py         # RemediationAgent — main entry point
├── state.py         # RemediationStateMachine
├── classifier.py    # Automation level classification
├── locator.py       # SourceLocator — DOM → source file mapping
├── analyzer.py      # SourceAnalyzer — context inspection
├── planner.py       # RemediationPlanner — LLM-assisted plan
├── patcher.py       # PatchGenerator — unified diff
├── validator.py     # PatchValidator — multi-gate checks
├── git_manager.py   # GitManager — branch/apply/rollback/commit/PR
├── test_runner.py   # TestExecutor
├── verifier.py      # VerificationEngine — before/after comparison
├── policy.py        # RemediationPolicy — configurable thresholds
├── schemas.py       # New Pydantic models
└── tools.py         # Controlled tool implementations

tests/unit/
├── test_remediation_classifier.py
├── test_source_locator.py
├── test_patch_generator.py
├── test_patch_validator.py
└── test_verification_engine.py

tests/integration/
└── test_remediation_agent.py

tests/benchmark/
└── remediation_benchmark.py
```

---

## 11. Files to Modify

| File | Change |
|---|---|
| `wcag/schemas.py` | Add RemediationStatus, RemediationAutomationLevel enums |
| `wcag/rag_engine.py` | Refactor contradiction model to SCRelationship |
| `config.py` | Add remediation settings section |
| `cli.py` | Add `remediate` subcommand |
| `reporting/generator.py` | Add remediation audit section |
| `api/routes.py` | Add POST `/api/v1/remediate` |
| `pyproject.toml` | Add `gitpython`, `unidiff` dependencies |

---

## 12. Source Localization Strategy

> **Critical principle**: Browser DOM ≠ source code. A `<button class="register-btn">` may
> come from a React component, a Django template, a CMS, or a CSS-in-JS file.
> Never assume identity.

### Layered Search (in priority order)

1. **Exact text search** — search for exact class/id/aria attribute values via ripgrep
2. **Selector decomposition** — split `.Portal_voteBtn` → `Portal`, `voteBtn` → search parts
3. **Accessible name search** — search for visible text content of the element
4. **Component name inference** — if class encodes component name, find that component file
5. **AST-level search** — parse JS/TSX and locate JSX elements by role/attribute
6. **Framework heuristics** — React=`*.tsx/*.jsx`, Vue=`*.vue`, Angular=`*.component.html`

### Confidence Rules

| Evidence | Confidence |
|---|---|
| Exact attribute match in one file | `DIRECT_MATCH` |
| Strong partial match in 1-2 files | `LIKELY_MATCH` |
| Match in 3+ files | `AMBIGUOUS_MATCH` |
| No match | `NOT_FOUND` |

---

## 13. Security Model

| Risk | Mitigation |
|---|---|
| Path traversal in repo_path | `allowed_directories` allowlist; `Path.resolve()` jail check |
| Secrets in source files sent to LLM | `settings.redact_patterns` applied before every LLM prompt |
| AI-generated patch modifying .env or CI files | Blocklist: `.env`, `.github/`, `secrets.*`, `credentials.*` |
| Patch modifying main branch | `create_branch()` always called first; direct main writes blocked at tool level |
| Infinite repair loops | `MAX_REMEDIATION_ATTEMPTS = 3` hard limit in state machine |
| Unrestricted subprocess access | All subprocess calls go through `tools.py` explicit allowlist |
| LLM hallucinating source paths | All paths validated with `Path.exists()` before any action |

---

## 14. Phase-by-Phase Implementation Plan

### PHASE 1 — Source Localization
- CREATE `remediation/schemas.py` (SourceLocation, SourceMatchConfidence)
- CREATE `remediation/locator.py` (SourceLocator)
- CREATE `tests/unit/test_source_locator.py`
- No LLM calls — pure ripgrep-based file search
- **Expected result**: Given a Finding with selector `.register-btn`, locate `src/components/Register.tsx:74`

### PHASE 2 — Source Analysis + Remediation Planning
- CREATE `remediation/analyzer.py`, `remediation/planner.py`, `remediation/classifier.py`
- EXTEND `config.py` with remediation settings
- LLM-assisted planning, but classification is deterministic-first

### PHASE 3 — Patch Generation (unified diff)
- CREATE `remediation/patcher.py`
- ADD `unidiff` to `pyproject.toml`
- **Validation**: `git apply --check` must succeed on fixture repo

### PHASE 4 — Patch Validation (8 independent gates)
- CREATE `remediation/validator.py`
- REFACTOR `wcag/rag_engine.py` contradiction model → SCRelationship

### PHASE 5 — Git Isolation
- CREATE `remediation/git_manager.py`
- ADD `gitpython` to `pyproject.toml`
- **Validation**: Only target file listed in `git diff --name-only`

### PHASE 6 — Patch Application
- EXTEND `remediation/git_manager.py` (apply, diff capture, scope verification)
- CREATE `remediation/tools.py` (controlled tool wrappers)

### PHASE 7 — Test Execution
- CREATE `remediation/test_runner.py`
- Captures: exit code, failing tests, duration, stdout/stderr

### PHASE 8 — Accessibility Re-scan
- CREATE wrapper in `remediation/verifier.py`
- Calls existing `ScanOrchestrator` with same URL + same axe tags

### PHASE 9 — Verification (Before/After)
- EXTEND `remediation/verifier.py`
- VERIFIED only when: target finding absent AND no new CONFIRMED findings AND tests pass

### PHASE 10 — Automatic Repair Loop
- CREATE `remediation/state.py` (RemediationStateMachine)
- Hard limit: `MAX_REMEDIATION_ATTEMPTS = 3`

### PHASE 11 — Rollback
- EXTEND `remediation/git_manager.py` (rollback, preserve evidence)

### PHASE 12 — Commit + PR Workflow
- EXTEND `remediation/git_manager.py`
- PR only if `create_pr=True` — never automatic

### PHASE 13 — Evaluation Framework
- CREATE `tests/benchmark/remediation_benchmark.py`

### PHASE 14 — Production Hardening
- Metrics, timeouts, full integration tests

---

## 15. CLI Design

```bash
# Dry-run: inspect and propose, do NOT touch source files
python -m accessibility_agent.cli remediate \
    --finding A11Y-XXXXXXXX \
    --finding-file reports/RUN-XXXX_report.json \
    --repo ./my-app \
    --dry-run

# Apply in isolated branch, verify, commit
python -m accessibility_agent.cli remediate \
    --finding A11Y-XXXXXXXX \
    --repo ./my-app \
    --apply \
    --create-branch \
    --verify \
    --auto-repair \
    --max-attempts 3 \
    --create-pr
```

---

## 16. Open Questions — MUST Answer Before Phase 1

> [!IMPORTANT]
> These questions must be decided before any code is written.

1. **Target source type for Phase 1**: Static HTML files only, or also React/TSX? Starting HTML-only dramatically reduces risk.

2. **Re-scan target**: Live URL (requires running app server) or local static file? Most patches require a rebuild before re-scanning.

3. **Test runner**: Do target repos have existing test suites? If not, should Phase 7 (test execution) be optional (warn) rather than blocking?

4. **PR creation API**: `PyGithub`, `gh` CLI, or GitHub REST API directly?

5. **Re-scan AI reasoning**: Disable LLM AI reasoning during re-scan by default to conserve Groq tokens?

6. **LLM structured output**: Migrate remediation LLM calls to use Groq function-calling for safer JSON extraction?

---

## 17. Risks and Limitations

| Risk | Severity | Mitigation |
|---|---|---|
| Source localization fails for minified/bundled code | High | Return `NOT_FOUND` → `MANUAL_REVIEW`; never guess |
| LLM hallucination of source file paths | High | All paths validated with `Path.exists()` before use |
| Patch causes silent semantic change | Medium | `APPLY_IN_BRANCH` default policy; human approval gates |
| Re-scan hits Groq rate limits | Medium | Key rotation already implemented; disable AI for re-scan |
| Framework diversity (React/Vue/Angular/Django) | High | Phase 1 = HTML-only; framework support added in later phases |
| Screen reader behavior not verifiable programmatically | Known | Always `screen_reader_verified=False`; documented in manual review package |
| Git operations on Windows paths | Medium | Use `pathlib.Path` with explicit forward-slash normalization |
| E2E test suite absent in target repo | Medium | `require_tests=False` mode; accessibility re-scan still mandatory |

---

*Phase 0 Complete. Awaiting review and approval before Phase 1 begins.*
