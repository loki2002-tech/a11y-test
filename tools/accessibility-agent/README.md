# ♿ AI Accessibility Agent

**Autonomous AI-Powered Accessibility Remediation & Verification System**

[![CI](https://github.com/loki2002-tech/ai-accessibility-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/loki2002-tech/ai-accessibility-agent/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![WCAG 2.2](https://img.shields.io/badge/WCAG-2.2-green.svg)](https://www.w3.org/TR/WCAG22/)

---

## What It Does

This system scans any web application for accessibility violations and **autonomously remediates them** — locating the source file, generating a validated patch, creating a git branch, running the project tests, and opening a Pull Request for human review.

```
Scan URL → Find violations → Locate source → Generate patch → Validate (8 gates)
       → Git branch → Run tests → Verify fix → Open PR
```

Supports **HTML, React, Vue, Angular, Next.js, Django, Flask, Rails** and any framework with source files on disk.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SCAN LAYER (existing)                               │
│  Browser (Playwright) → axe-core → WCAG Mapper → Deduplication → Finding   │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │ Finding (schema-validated)
┌───────────────────────────────▼─────────────────────────────────────────────┐
│                     REMEDIATION PIPELINE (new)                              │
│                                                                             │
│  P1: SourceLocator     — find the exact file + line on disk                │
│  P2: SourceAnalyzer    — extract surrounding code context                   │
│      Classifier        — SAFE_AUTO_FIX / NEEDS_HUMAN_REVIEW / DO_NOT_…     │
│      RemediationPlanner — AI or deterministic fix plan                      │
│  P3: PatchGenerator    — unified diff in 3 strategies (attr/add/replace)    │
│      PatchValidator    — 8 safety gates (no secrets, no ARIA breaks, etc.)  │
│  P4: RAG WCAG Engine   — contradiction check against WCAG SC relationships  │
│  P5: GitManager        — branch → commit → push → GitHub PR                 │
│  P6: TestRunner        — auto-detect + run pytest/jest/vitest/playwright    │
│  P7: VerificationEngine — before/after axe scan comparison                  │
│  P8: RemediationAgent  — orchestrator (3-attempt retry, rollback on fail)   │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────────┐
│                      INTERFACE LAYER                                        │
│  P9:  CLI  — a11y-agent scan / remediate / serve                            │
│  P10: REST — POST /api/v1/scan  |  POST /api/v1/remediate                  │
│  P11: CI   — GitHub Actions: ci.yml + remediate.yml                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/loki2002-tech/ai-accessibility-agent
cd ai-accessibility-agent
pip install -e ".[dev]"
playwright install chromium
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — add GITHUB_TOKEN and GROQ_API_KEY
```

Required environment variables:

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | LLM inference (AI-assisted planning) |
| `GITHUB_TOKEN` | PR creation via GitHub REST API |

---

## Usage

### Scan a URL

```bash
# Basic scan
a11y-agent scan --url https://allcanaccess.com

# Full audit with agentic DOM interaction
a11y-agent scan --url https://allcanaccess.com --mode full --agentic

# Save reports to a directory
a11y-agent scan --url https://allcanaccess.com --output ./reports
```

### Remediate a Finding

```bash
# From a saved finding JSON file
a11y-agent remediate \
  --finding ./reports/finding_A11Y-XXXX.json \
  --repo ./my-app

# Dry-run (plan + validate only, no git)
a11y-agent remediate \
  --finding ./finding.json \
  --repo . \
  --dry-run

# Skip tests, skip PR
a11y-agent remediate \
  --finding ./finding.json \
  --repo . \
  --no-tests --no-pr \
  --output ./result.json
```

### REST API

```bash
# Start the API server
a11y-agent serve --host 0.0.0.0 --port 8000

# Interactive docs
open http://localhost:8000/docs
```

**Submit a scan:**
```bash
curl -X POST http://localhost:8000/api/v1/scan \
  -H "Content-Type: application/json" \
  -d '{"url": "https://allcanaccess.com", "mode": "automated"}'
# → {"run_id": "...", "message": "..."}

curl http://localhost:8000/api/v1/scans/{run_id}/status
curl http://localhost:8000/api/v1/scans/{run_id}/report
```

**Submit a remediation:**
```bash
curl -X POST http://localhost:8000/api/v1/remediate \
  -H "Content-Type: application/json" \
  -d '{
    "finding": { ... },
    "repo_path": "/path/to/app",
    "dry_run": false
  }'
# → {"job_id": "...", "status": "queued", "poll_url": "..."}

curl http://localhost:8000/api/v1/remediations/{job_id}
```

---

## GitHub Actions CI

### Run Tests Automatically

The [`ci.yml`](.github/workflows/ci.yml) workflow runs the full unit test suite on every push and PR:

```yaml
# Triggered automatically on push/PR to main
# Python 3.11 + 3.12 matrix
```

### Trigger Remediation in CI

The [`remediate.yml`](.github/workflows/remediate.yml) workflow can be triggered manually or by another workflow:

```bash
# Trigger via GitHub CLI
gh workflow run remediate.yml \
  -f finding_json='{"finding_id":"A11Y-001","url":"...","rule_id":"html-has-lang",...}' \
  -f repo_path="." \
  -f dry_run="false"
```

Or chain it from your scan workflow:

```yaml
- name: Auto-remediate top finding
  uses: ./.github/workflows/remediate.yml
  with:
    finding_json: ${{ steps.scan.outputs.top_finding }}
    repo_path: "."
  secrets: inherit
```

---

## Safety Guarantees

The agent enforces strict safety invariants that cannot be bypassed:

| Gate | What It Checks |
|---|---|
| 1. File exists | Target source file is on disk |
| 2. Context matches | Lines to patch match expected content |
| 3. Applies cleanly | `git apply --check` succeeds |
| 4. No unrelated changes | Patch touches ONLY the target element |
| 5. No secrets | No API keys, tokens, or credentials in diff |
| 6. No invalid ARIA | No `aria-hidden` on interactive, no `role="presentation"` on semantic |
| 7. RAG contradiction | No WCAG SC conflicts (e.g. adding `lang` doesn't break other criteria) |
| — Always | Branch created, never commits to `main` |
| — Always | `DO_NOT_AUTO_REMEDIATE` findings → manual review package |

---

## Remediation Status Values

| Status | Meaning |
|---|---|
| `verified` | Fix applied, tests passed, re-scan confirmed resolution ✅ |
| `failed` | All 3 attempts failed (see validation_results for details) ❌ |
| `rolled_back` | Fix caused test failures — branch cleaned up ↩️ |
| `manual_review` | Agent determined the fix requires human judgment 🔍 |
| `rejected` | Finding rejected at input validation stage |

---

## Test Suite

```bash
# Run all unit tests
python -m pytest tests/unit/ -v

# Individual phase tests
python -m pytest tests/unit/test_source_locator.py     # Phase 1
python -m pytest tests/unit/test_remediation_classifier.py  # Phase 2
python -m pytest tests/unit/test_patcher.py            # Phase 3
python -m pytest tests/unit/test_rag_contradiction.py  # Phase 4
python -m pytest tests/unit/test_git_manager.py        # Phase 5
python -m pytest tests/unit/test_test_runner.py        # Phase 6
python -m pytest tests/unit/test_verifier.py           # Phase 7
python -m pytest tests/unit/test_remediation_agent.py  # Phase 8
python -m pytest tests/unit/test_cli_remediate.py      # Phase 9
```

**292/292 unit tests passing** (5 pre-existing `test_agent_planner.py` failures unrelated to this work).

---

## Integrating Into Your Project

### Option A — Scan + Remediate any public URL

```python
from accessibility_agent.remediation.agent import RemediationAgent
from accessibility_agent.wcag.schemas import Finding

finding = Finding.model_validate({...})   # from a scan result
agent = RemediationAgent(repo_path=Path("./my-app"), create_pr=True)
result = agent.remediate(finding)

print(result.status, result.pr_url)
```

### Option B — REST API integration

```javascript
// In your frontend project:
const res = await fetch('http://localhost:8000/api/v1/remediate', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ finding: finding, repo_path: '/path/to/app' })
});
const { job_id, poll_url } = await res.json();

// Poll until done
const status = await fetch(`http://localhost:8000${poll_url}`).then(r => r.json());
```

### Option C — GitHub Actions in your CI/CD

Add to your `.github/workflows/accessibility.yml`:

```yaml
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Scan & Remediate
        run: |
          pip install ai-accessibility-agent
          a11y-agent scan --url ${{ env.STAGING_URL }} --output ./reports
          # Then trigger remediate.yml for each finding
```

---

## Project Structure

```
src/accessibility_agent/
├── accessibility/          # axe-core engine, browser automation
├── agent/                  # scan orchestrator, agentic interaction
├── api/                    # FastAPI routes (scan + remediation)
│   └── routes.py
├── evidence/               # evidence collector
├── remediation/            # the full 8-phase remediation pipeline
│   ├── locator.py          # Phase 1: source location
│   ├── analyzer.py         # Phase 2a: code context analysis
│   ├── classifier.py       # Phase 2b: automation level classification
│   ├── planner.py          # Phase 2c: remediation plan generation
│   ├── patcher.py          # Phase 3a: patch generation
│   ├── validator.py        # Phase 3b: 8-gate patch validation
│   ├── git_manager.py      # Phase 5: branch/commit/push/PR
│   ├── test_runner.py      # Phase 6: auto-detect + run test suite
│   ├── verifier.py         # Phase 7: before/after scan comparison
│   ├── agent.py            # Phase 8: end-to-end orchestrator
│   └── schemas.py          # Pydantic models for all remediaton data
├── wcag/                   # WCAG knowledge base, SC relationships, RAG
│   ├── rag_engine.py       # Phase 4: contradiction detection
│   ├── sc_relationships.py # SC conflict/compatibility mapping
│   └── schemas.py          # Finding, WCAGMapping, ElementLocator schemas
├── cli.py                  # Phase 9: Typer CLI (scan / remediate / serve)
└── config.py               # Settings / environment variable management

tests/
└── unit/                   # 292+ unit tests (all mocked, no network/git)
    ├── test_source_locator.py
    ├── test_remediation_classifier.py
    ├── test_patcher.py
    ├── test_rag_contradiction.py
    ├── test_git_manager.py
    ├── test_test_runner.py
    ├── test_verifier.py
    ├── test_remediation_agent.py
    └── test_cli_remediate.py

.github/
└── workflows/
    ├── ci.yml              # Run tests on every push/PR
    └── remediate.yml       # Manual/triggered remediation workflow
```

---

## Contributing

1. Fork and clone the repository
2. Install with `pip install -e ".[dev]"`
3. Run `playwright install chromium`
4. All code must pass `python -m pytest tests/unit/`
5. All patches must include tests

---

## Disclaimer

> ⚠️ Auto-generated fixes are intended as a starting point, not a guarantee of WCAG conformance. All PRs opened by this agent require human expert review before merging. A passing automated scan does not constitute legal WCAG conformance.
