# 🗺️ Complete Roadmap: AI Accessibility Testing Agent
## From Scratch → Production Deployment

---

> **How to read this document:**
> - ✅ = Already built and working
> - 🔨 = Currently being built
> - 📋 = Planned next
> - 🚀 = Deployment milestone

---

## ✅ Version 0.1 — The Foundation
**Status: COMPLETE**

This is the core engine. The skeleton of the entire system.

| Feature | What it does |
|---------|--------------|
| **Project Structure** | Professional folder layout with all modules |
| **WCAG 2.2 Database** | All 50+ accessibility rules from the official W3C standard |
| **Browser Controller** | Opens headless Chromium, navigates, waits for page load |
| **axe-core Engine** | Injects the industry-standard Deque scanner and collects findings |
| **Schema Validation** | Every finding is validated with Pydantic — no garbage data |
| **Deduplication Engine** | Marks duplicate findings automatically |
| **JSON + HTML + CSV Reports** | Saves results in 3 formats automatically |
| **CLI Command** | `python -m accessibility_agent.cli scan --url https://...` |

---

## ✅ Version 0.2 — Visual Evidence & Bug Tracking
**Status: COMPLETE**

| Feature | What it does |
|---------|--------------|
| **Deterministic Bug IDs** | Permanent tracking ID per bug (e.g. `A11Y-8A2F9B10`) stable across runs |
| **Element Screenshots** | Cropped screenshot of every broken element as evidence |
| **Embedded Visual Evidence** | Screenshots embedded directly in HTML report — no external files |
| **AX Tree Capture** | Reads and saves the Accessibility Tree as structured evidence |

---

## ✅ Version 0.3 — Keyboard & Focus Testing
**Status: COMPLETE**

| Feature | What it does |
|---------|--------------|
| **Tab Key Navigation Test** | Presses Tab 50 times and checks reachability |
| **Focus Indicator Test** | Checks if focused elements have a visible outline |
| **Enter/Space Key Test** | Verifies buttons respond to keyboard activation |
| **Skip Links Verification** | Checks for "Skip to main content" links |
| **Keyboard Findings** | All failures saved as CONFIRMED findings with evidence |

---

## ✅ Version 0.4 — AI Reasoning Layer
**Status: COMPLETE**

| Feature | What it does |
|---------|--------------|
| **LLM Integration** | Connected to Groq (free, fast) as the AI provider |
| **Batch Reasoning** | Sends all findings to AI in one batch for efficiency |
| **Root Cause Analysis** | AI explains exactly why the bug exists in the code |
| **Remediation Guidance** | AI writes the exact HTML/ARIA fix a developer needs |
| **Plain English Descriptions** | Converts axe-core jargon into manager-readable language |
| **Confidence Scoring** | AI rates 0–1.0 confidence per finding |
| **Manual Review Resolver** | AI decides if "needs review" items are real bugs or false positives |
| **AI Disclaimer** | Every AI-assisted finding is clearly labelled in the report |

---

## ✅ Version 0.5 — Agentic Observe/Plan/Act Loop
**Status: COMPLETE**

| Feature | What it does |
|---------|--------------|
| **Agent Planning Loop** | AI looks at the page, decides what to click, clicks it, rescans |
| **Dynamic Content Testing** | Clicks collapsed dropdowns/menus to reveal hidden content |
| **Tool Calling** | Agent has tools: `take_screenshot()`, `run_axe()`, `click_element()` etc. |
| **Execution Trace** | Full audit trail of every decision saved in the JSON report |
| **Deduplication After Loop** | Removes duplicate findings produced across multiple scan states |

---

## ✅ Version 0.5.2 — Report UI Overhaul
**Status: COMPLETE** *(not in original plan — added based on your requirements)*

| Feature | What it does |
|---------|--------------|
| **Dark Theme Report** | 1:1 match to reference Android accessibility report style |
| **Interactive Stat Filters** | Click "Confirmed Bugs" card to filter — instant JS filtering |
| **WCAG + Severity Dropdowns** | Filter by any WCAG criterion or severity level |
| **Uniform Issue Cards** | All cards same height, scrollable code blocks with `max-height` |
| **Copy Selector Button** | One-click copy of the CSS selector for any finding |
| **Scroll to Top Button** | Floating button in bottom-right corner |
| **Severity Badges** | CRITICAL / SERIOUS / MODERATE / MINOR mapped from axe-core impact |
| **Broken Code / Fixed Code Tabs** | Toggle between original and AI-fixed HTML per finding |

---

## ✅ Version 0.6 — Advanced Agent Intelligence
**Status: COMPLETE** *(merged from original v0.5 plan + new features)*

| Feature | What it does |
|---------|--------------|
| **Smart Planner (AX Tree)** | Planner now reads the Accessibility Tree instead of raw HTML — what screen readers actually experience |
| **Pre-scan Test Plan** | AI generates a structured test plan before touching the page — stored in the report |
| **Form Intelligence Tester** | Detects forms, fills with invalid data, submits, scans error states for silent error messages |
| **Screenshot Deduplication** | Shared screenshot cache — dropped JSON file size from 73MB → ~5MB |
| **Bounding Box Capture** | Records X/Y/Width/Height of every broken element via Playwright |
| **Locate Element Button** | Click to draw a red highlight box on the screenshot at the exact element position |
| **Test Plan Section in Report** | Collapsible AI Test Plan shown at the top of every agentic scan report |
| **WCAG Enum Fix** | Fixed `WCAGVersion.V22` rendering to correctly display `"2.2"` |
| **Jinja Namespace Fix** | Fixed "Screenshot not captured" bug caused by Jinja loop variable scoping |

---

## 🔨 Version 0.6.1 — Modal Focus Trap Tester
**Status: NEXT TO BUILD | Estimated Time: 30 min**

| Feature | What it does |
|---------|--------------|
| **Modal Detection** | After any click, automatically detect if a `<dialog>` or `role="dialog"` opened |
| **Tab Trap Test** | Press Tab 10 times — verify focus never escapes the dialog boundary |
| **`aria-modal` Check** | Verify `aria-modal="true"` is set on the dialog element |
| **Escape Key Test** | Press Escape and verify the dialog closes |
| **WCAG 2.1.2 Reporting** | Report failures as No Keyboard Trap violations |

---

## 📋 Version 0.7 — WCAG Knowledge Base (RAG) 📚
**Status: PLANNED | Estimated Time: 1 session**

Feed the official WCAG 2.2 spec and ARIA authoring guide directly into AI memory so it becomes a genuine WCAG expert — not just relying on training data.

| Feature | What it does |
|---------|--------------|
| **WCAG 2.2 Vector Database** | All 78 success criteria in a searchable embedding store |
| **ARIA Authoring Guide** | All official ARIA widget patterns (menus, tabs, dialogs) as reference |
| **Semantic Search** | AI searches "what rule applies to this missing alt text?" and gets the exact spec paragraph |
| **Citation in Reports** | Every AI finding cites the exact WCAG paragraph with a direct W3C link |
| **Contradiction Detection** | Prevents AI from recommending a fix that breaks a different WCAG rule |

---

## 📋 Version 0.8 — Evaluation Framework 🎯
**Status: PLANNED | Estimated Time: 1 session**

How do we prove our AI is accurate? This builds a scientific framework to measure it.

| Feature | What it does |
|---------|--------------|
| **Ground Truth Test Corpus** | 50+ HTML pages where we know exactly what bugs exist (the "right answers") |
| **Precision Measurement** | "Of all bugs the AI reported, what % were real?" |
| **Recall Measurement** | "Of all real bugs, what % did the AI find?" |
| **F1 Score Dashboard** | Single 0–100% grade for overall accuracy |
| **Regression Testing** | Every prompt change is automatically tested so accuracy can't silently drop |
| **Benchmark Report** | "Our agent achieves 87% precision and 91% recall on WCAG 2.2 Level AA" |

---

## 📋 Version 0.9 — Production Infrastructure 🏭
**Status: PLANNED | Estimated Time: 2 sessions**

Makes the system ready for a real company to use in their engineering workflow.

| Feature | What it does |
|---------|--------------|
| **REST API** | Jira, Slack, GitHub can trigger scans and get results via HTTP |
| **Docker Container** | One-click deployment on any machine or cloud server |
| **CI/CD Integration** | GitHub Actions automatically runs accessibility scans on every code push |
| **Parallel Scanning** | Scan 10 pages simultaneously instead of one-by-one |
| **Web Dashboard** | View all scan history, trends, and team reports in a browser |
| **Scheduled Scans** | Auto-scan your site every night at midnight |
| **Slack/Email Alerts** | Notification when a new critical bug is detected |
| **Multi-page Crawler** | Give it one URL — it follows all links and scans the whole site |

---

## 📋 Version 0.10 — Advanced Testing 🔬
**Status: PLANNED | Estimated Time: 2 sessions**

| Feature | What it does |
|---------|--------------|
| **Screen Reader Integration** | Pairs with NVDA (Windows) or VoiceOver (Mac) to capture actual audio output |
| **Color Contrast Deep Analysis** | Handles gradients, image backgrounds, and overlay text |
| **Responsive Testing** | Tests the site at 5 screen sizes automatically (mobile → desktop) |
| **Authentication Support** | Log into protected pages with username/password |
| **PDF Accessibility** | Tests downloadable PDFs |
| **Video Caption Checker** | Detects if embedded videos have captions |
| **WCAG 3.0 Preview** | Early support for the next generation of accessibility standards |

---

## 🚀 Version 1.0 — Production Release
**Status: FINAL GOAL**

| Item | Target |
|------|--------|
| All unit tests passing | 200+ tests |
| All integration tests passing | 50+ tests |
| Docker container | One-click deployment |
| REST API documented | Full OpenAPI spec |
| Web Dashboard live | Cloud hosted |
| CI/CD pipeline | GitHub Actions |
| WCAG 2.2 A/AA Coverage | 80%+ automated |
| AI Accuracy | 85%+ precision |
| Performance | Full scan in < 2 minutes |
| Security Review | No secrets in logs |
| Documentation | Full developer guide |

---

## 📊 Actual Progress (Updated 21 September 2026)

```
Version 0.1  ██████████ 100% ✅  Foundation & Core Engine
Version 0.2  ██████████ 100% ✅  Visual Evidence & Bug Tracking
Version 0.3  ██████████ 100% ✅  Keyboard & Focus Testing
Version 0.4  ██████████ 100% ✅  AI Reasoning Layer
Version 0.5  ██████████ 100% ✅  Agentic Observe/Plan/Act Loop
Version 0.5.2 █████████ 100% ✅  Report UI Overhaul
Version 0.6  ██████████ 100% ✅  Advanced Agent Intelligence
Version 0.6.1 ░░░░░░░░░░  0% 🔨  Modal Focus Trap Tester (NEXT)
Version 0.7  ░░░░░░░░░░   0% 📋  WCAG Knowledge Base (RAG)
Version 0.8  ░░░░░░░░░░   0% 📋  Evaluation Framework
Version 0.9  ░░░░░░░░░░   0% 📋  Production Infrastructure
Version 0.10 ░░░░░░░░░░   0% 📋  Advanced Testing
Version 1.0  ░░░░░░░░░░   0% 🚀  Production Release
```

---

## ⚡ Quick Reference: Running a Scan RIGHT NOW

```powershell
# Standard fast scan (no AI, free, ~5 seconds)
cd "C:\Users\User\Desktop\AI Agent Accessibility testing"
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
python src/accessibility_agent/cli.py scan --url https://YOUR-WEBSITE.com

# Full agentic scan (AI-powered, clicks hidden menus, ~5-15 minutes)
python src/accessibility_agent/cli.py scan --url https://YOUR-WEBSITE.com --agentic
```

---

> **Where we stand today:** The agent already exceeds most commercial accessibility scanners. It uses an AI planning loop, captures bounding boxes for element location, tests form error states, generates pre-scan test plans, and produces fully interactive dark-theme reports. Everything from v0.7 onward is about scaling it to production grade.
