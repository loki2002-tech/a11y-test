"""
Report Generator.

Generates accessibility testing reports in multiple formats:
1. JSON — machine-readable, schema-validated
2. HTML — human-readable with embedded screenshots, evidence, and remediation
3. CSV — for spreadsheet-based review workflows

Design principles:
- Reports are GENERATED FROM validated Finding objects, not from LLM text.
- Screenshots are embedded as data URIs in HTML reports.
- Every finding links back to its evidence.
- Reports include an explicit disclaimer that they do not constitute
  WCAG conformance determination.

The HTML template uses Jinja2.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape, BaseLoader, DictLoader

from accessibility_agent.config import settings
from accessibility_agent.logging_config import get_logger
from accessibility_agent.wcag.schemas import (
    Finding,
    FindingStatus,
    ScanResult,
    WCAGLevel,
)

log = get_logger(__name__)


# ── HTML Template ─────────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Accessibility Report — {{ result.url }}</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    :root { --bg: #0a0f1a; --surface: #111827; --surface2: #1f2937; --border: #64748b; --text: #f9fafb; --text-muted: #cbd5e1; --accent: #fca5a5; --accent2: #f97316; }
    body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; padding-bottom: 80px; }
    
    /* Hero */
    .hero { background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 50%, #0f172a 100%); padding: 60px 20px 40px; text-align: center; border-bottom: 1px solid var(--border); }
    .hero h1 { font-size: 3rem; font-weight: 800; background: linear-gradient(135deg, #f8fafc 0%, #818cf8 50%, #f8fafc 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 12px; letter-spacing: -1px; }
    .hero .subtitle { display: flex; flex-wrap: wrap; justify-content: center; gap: 12px; margin-top: 20px; }
    .hero .subtitle span { background: rgba(255,255,255,0.03); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.08); color: #cbd5e1; font-size: 0.85rem; font-weight: 500; padding: 6px 14px; border-radius: 9999px; display: inline-flex; align-items: center; gap: 6px; }
    .hero .subtitle span b { color: #e2e8f0; font-weight: 600; }
    
    .container { max-width: 1400px; margin: -20px auto 40px; padding: 0 20px; position: relative; z-index: 10; }
    
    /* Stats as Filters */
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
    .stat { background: var(--surface); border: 2px solid var(--border); border-radius: 12px; padding: 20px; text-align: center; cursor: pointer; transition: transform 0.2s, border-color 0.2s, background 0.2s; user-select: none; }
    .stat:hover { transform: translateY(-3px); border-color: #94a3b8; }
    .stat.active-filter { background: #1e293b; border-color: #818cf8; box-shadow: 0 0 15px rgba(129, 140, 248, 0.2); }
    .stat .number { font-size: 2.2rem; font-weight: 800; margin-bottom: 4px; }
    .stat .label { color: #cbd5e1; font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }

    /* Dropdown Filters */
    .filter-bar { display: flex; gap: 16px; margin-bottom: 30px; flex-wrap: wrap; background: var(--surface); padding: 16px; border-radius: 12px; border: 1px solid var(--border); }
    .filter-bar select { background: var(--surface2); color: #fff; border: 1px solid var(--border); padding: 10px 16px; border-radius: 8px; font-family: inherit; font-size: 0.9rem; outline: none; cursor: pointer; flex: 1; min-width: 200px; }
    .filter-bar select:focus { border-color: #818cf8; }
    .filter-status-text { width: 100%; color: #94a3b8; font-size: 0.9rem; margin-top: 8px; font-weight: 500; display: none; }

    /* Cards */
    .violations-list { display: flex; flex-direction: column; gap: 24px; }
    .violation-card { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; display: grid; grid-template-columns: 400px 1fr; align-items: stretch; transition: display 0.3s; }
    @media (max-width: 1024px) { .violation-card { grid-template-columns: 1fr; } }
    
    .card-screenshot { background: #18212f; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 24px; border-right: 1px solid var(--border); overflow: hidden; position: relative; }
    .screenshot-container { position: relative; max-width: 100%; display: inline-block; cursor: zoom-in; }
    .card-screenshot img { max-width: 100%; max-height: 500px; object-fit: contain; border-radius: 8px; border: 1px solid #334155; box-shadow: 0 8px 24px rgba(0,0,0,0.4); display: block; transition: transform 0.2s; }
    .card-screenshot img:hover { transform: scale(1.02); }
    
    /* Fullscreen Modal */
    .lightbox {
      display: none; position: fixed; z-index: 9999; left: 0; top: 0; width: 100%; height: 100%;
      background-color: rgba(0,0,0,0.9); overflow: auto; align-items: center; justify-content: center;
    }
    .lightbox.active { display: flex; flex-direction: column; }
    .lightbox-content-wrapper { position: relative; max-width: 95%; max-height: 90vh; }
    .lightbox-img { max-width: 100%; max-height: 90vh; object-fit: contain; box-shadow: 0 0 40px rgba(0,0,0,0.8); border: 1px solid #334155; }
    .lightbox-close {
      position: absolute; top: 20px; right: 30px; color: #fff; font-size: 40px; font-weight: bold; cursor: pointer; transition: color 0.2s;
    }
    .lightbox-close:hover { color: #818cf8; }

    .locator-box {
      position: absolute; display: none; pointer-events: none;
      box-shadow: 0 0 0 3px #b82dd2, 0 0 10px 3px rgba(184,45,210,0.8);
      border: 2px solid #fff; z-index: 10;
      border-radius: 2px;
    }
    .locator-tooltip {
      position: absolute; top: 100%; left: -2px; margin-top: 8px;
      background: #fff; color: #111827; border: 1px solid #111827;
      padding: 6px 10px; border-radius: 4px; font-size: 0.75rem; font-family: 'Inter', sans-serif;
      box-shadow: 0 4px 12px rgba(0,0,0,0.3); width: max-content; min-width: 180px; z-index: 20;
    }
    .locator-tooltip b { color: #b82dd2; font-family: monospace; font-size: 0.8rem; }
    .locator-tooltip .tt-row { display: flex; justify-content: space-between; margin-top: 4px; color: #4b5563; }
    .locator-tooltip .tt-row span:first-child { color: #6b7280; }
    .locator-tooltip::before {
      content: ''; position: absolute; top: -5px; left: 10px;
      width: 8px; height: 8px; background: #fff; border-left: 1px solid #111827; border-top: 1px solid #111827;
      transform: rotate(45deg);
    }
    
    .no-screenshot { color: #64748b; font-style: italic; font-size: 0.9rem; }
    
    .card-details { padding: 24px 32px; display: flex; flex-direction: column; gap: 16px; }
    .badge-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .severity-badge { padding: 4px 12px; border-radius: 6px; font-size: 0.75rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px; border: 1px solid; }
    
    .badge-critical { background: rgba(220, 38, 38, 0.15); color: #ef4444; border-color: rgba(239, 68, 68, 0.3); }
    .badge-serious { background: rgba(234, 88, 12, 0.15); color: #f97316; border-color: rgba(249, 115, 22, 0.3); }
    .badge-moderate { background: rgba(202, 138, 4, 0.15); color: #eab308; border-color: rgba(234, 179, 8, 0.3); }
    .badge-minor { background: rgba(37, 99, 235, 0.15); color: #60a5fa; border-color: rgba(96, 165, 250, 0.3); }
    .badge-wcag { background: #1e293b; border-color: #475569; color: #cbd5e1; }
    
    .violation-title { font-size: 1.25rem; font-weight: 700; line-height: 1.3; color: #f8fafc; }
    
    .explanation-block { background: rgba(0,0,0,0.2); border-radius: 8px; padding: 16px; border-left: 4px solid #818cf8; }
    .explanation-block h4 { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 1px; color: #94a3b8; margin-bottom: 6px; }
    .explanation-block p { font-size: 0.95rem; color: #e2e8f0; line-height: 1.6; }
    .explanation-block p:not(:last-child) { margin-bottom: 12px; }

    /* Buttons block */
    .button-group { display: flex; gap: 8px; margin-top: 12px; }
    
    .xpath-container { display: flex; align-items: center; gap: 12px; background: #0f172a; padding: 10px 16px; border-radius: 8px; border: 1px solid #334155; }
    .xpath-container span { color: #94a3b8; font-size: 0.85rem; font-weight: 700; text-transform: uppercase; }
    .xpath-container code { color: #a78bfa; font-family: ui-monospace, SFMono-Regular, monospace; font-size: 0.85rem; word-break: break-all; flex: 1; }
    .btn-secondary { background: #334155; color: #f8fafc; border: 1px solid #475569; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 0.75rem; font-weight: 700; transition: background 0.2s; display: flex; align-items: center; gap: 6px; }
    .btn-secondary:hover { background: #475569; }
    .copy-btn { background: #3b82f6; color: #fff; border: none; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 0.75rem; font-weight: 700; transition: background 0.2s; display: flex; align-items: center; gap: 6px; }
    .copy-btn:hover { background: #2563eb; }
    .btn-locate { background: #818cf8; color: #fff; border: none; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 0.75rem; font-weight: 700; transition: background 0.2s; display: flex; align-items: center; gap: 6px; }
    .btn-locate:hover { background: #6366f1; }
      .test-plan-section { background: var(--surface); border: 1px solid #334155; border-radius: 12px; margin-bottom: 24px; overflow: hidden; }
    .test-plan-section summary { padding: 16px 20px; cursor: pointer; display: flex; align-items: center; gap: 8px; user-select: none; }
    .test-plan-body { padding: 0 20px 20px; }
      .test-plan-steps { display: flex; flex-direction: column; gap: 10px; }
      .test-plan-step { display: flex; gap: 14px; align-items: flex-start; background: var(--surface2); border-radius: 8px; padding: 12px 16px; border-left: 3px solid #818cf8; }
      .step-number { font-size: 1.2rem; font-weight: 800; color: #818cf8; min-width: 28px; }
      .step-body { flex: 1; }
      .step-action { display: inline-block; background: #1e1b4b; color: #818cf8; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.08em; padding: 2px 8px; border-radius: 4px; margin-bottom: 4px; }
      .step-target { color: #e2e8f0; font-weight: 600; font-size: 0.9rem; margin-bottom: 4px; font-family: 'Courier New', monospace; }
      .step-reason { color: #94a3b8; font-size: 0.85rem; margin-bottom: 8px; }
      .step-wcag { display: flex; gap: 6px; flex-wrap: wrap; }
    .copy-btn.copied { background: #10b981; }

    /* AI Remediation */
    .fix-block { background: rgba(16,185,129,0.05); border-radius: 12px; padding: 20px; border: 1px solid rgba(16,185,129,0.2); }
    .fix-block h4 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 1px; color: #10b981; margin-bottom: 8px; font-weight: 800; }
    
    .code-tabs { display: flex; gap: 8px; margin-bottom: 12px; }
    .code-tab { background: #1e293b; border: 1px solid #334155; color: #94a3b8; padding: 6px 14px; border-radius: 6px; font-size: 0.8rem; cursor: pointer; font-weight: 600; transition: all 0.2s; }
    .code-tab.active { background: #10b981; border-color: #10b981; color: #022c22; }
    .code-tab:hover:not(.active) { background: #334155; color: #fff; }
    
    /* Scrollable Code Blocks to prevent huge vertical stretching */
    .code-block { background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 16px; overflow-x: auto; max-height: 250px; overflow-y: auto; }
    .code-block pre { color: #e2e8f0; font-size: 0.85rem; line-height: 1.5; font-family: ui-monospace, SFMono-Regular, monospace; white-space: pre-wrap; word-break: break-word; margin: 0; }
    
    /* Scrollbar styling for code blocks */
    .code-block::-webkit-scrollbar { width: 8px; height: 8px; }
    .code-block::-webkit-scrollbar-track { background: #0f172a; border-radius: 4px; }
    .code-block::-webkit-scrollbar-thumb { background: #475569; border-radius: 4px; }
    .code-block::-webkit-scrollbar-thumb:hover { background: #64748b; }

    .wcag-ref { font-size: 0.8rem; color: #94a3b8; font-weight: 600; margin-top: auto; padding-top: 16px; border-top: 1px dotted var(--border); }
    
    /* Scroll to Top */
    .scroll-top { position: fixed; bottom: 30px; right: 30px; background: #818cf8; color: #fff; width: 50px; height: 50px; border-radius: 50%; display: flex; align-items: center; justify-content: center; cursor: pointer; opacity: 0; pointer-events: none; transition: opacity 0.3s, transform 0.2s; border: none; box-shadow: 0 4px 12px rgba(0,0,0,0.3); z-index: 1000; font-size: 1.5rem; }
    .scroll-top.visible { opacity: 1; pointer-events: auto; }
    .scroll-top:hover { transform: translateY(-3px); background: #6366f1; }

    .warning-card { background: #fffbeb; border: 1px solid #fcd34d; border-radius: 8px; padding: 16px; margin-top: 16px; margin-bottom: 16px; }
    .warning-card h4 { color: #b45309; margin: 0 0 8px 0; font-size: 0.95rem; display: flex; align-items: center; gap: 6px; }
    .warning-card ul { margin: 0; padding-left: 20px; color: #92400e; font-size: 0.9rem; }
    .footer { text-align: center; padding: 40px; color: #64748b; font-size: 0.85rem; border-top: 1px solid var(--border); margin-top: 60px; }
  </style>
  <script>
    // Tab switching for code blocks
    function switchTab(btn, showId, hideId) {
      const parent = btn.closest('.fix-block');
      parent.querySelectorAll('.code-tab').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      
      const showEl = parent.querySelector('#' + showId);
      const hideEl = parent.querySelector('#' + hideId);
      if(showEl) showEl.style.display = 'block';
      if(hideEl) hideEl.style.display = 'none';
    }

    // Copy to clipboard
    
    function locateElement(btn) {
      const container = btn.closest('.card-screenshot').querySelector('.screenshot-container');
      const img = container.querySelector('.full-page-img');
      const box = container.querySelector('.locator-box');
      
      if (!img || !box) return;
      
      if (img.naturalWidth === 0) {
          img.onload = () => locateElement(btn);
          return;
      }
      
      const scaleX = img.clientWidth / img.naturalWidth;
      const scaleY = scaleX;
      
      const origX = parseFloat(box.dataset.x);
      const origY = parseFloat(box.dataset.y);
      const origW = parseFloat(box.dataset.w);
      const origH = parseFloat(box.dataset.h);
      
      box.style.left = (origX * scaleX) + 'px';
      box.style.top = (origY * scaleY) + 'px';
      box.style.width = (origW * scaleX) + 'px';
      box.style.height = (origH * scaleY) + 'px';
      
      if (box.style.display === 'block') {
          box.style.display = 'none';
      } else {
          box.style.display = 'block';
          // Ensure container is scrolled if needed
          const card = btn.closest('.card-screenshot');
          if (card) {
            card.scrollTop = (origY * scaleY) - 50;
          }
      }
    }

    function openLightbox(imgElement) {
      const container = imgElement.closest('.screenshot-container');
      const box = container.querySelector('.locator-box');
      
      const lightbox = document.getElementById('lightbox');
      const lbImg = document.getElementById('lb-img');
      const lbBox = document.getElementById('lb-box');
      const lbTooltip = document.getElementById('lb-tooltip');
      
      lbImg.src = imgElement.src;
      
      lightbox.classList.add('active');
      
      lbImg.onload = () => {
        if (!box) {
          lbBox.style.display = 'none';
          return;
        }
        
        const scaleX = lbImg.clientWidth / lbImg.naturalWidth;
        const scaleY = scaleX;
        
        const origX = parseFloat(box.dataset.x);
        const origY = parseFloat(box.dataset.y);
        const origW = parseFloat(box.dataset.w);
        const origH = parseFloat(box.dataset.h);
        
        lbBox.style.left = (origX * scaleX) + 'px';
        lbBox.style.top = (origY * scaleY) + 'px';
        lbBox.style.width = (origW * scaleX) + 'px';
        lbBox.style.height = (origH * scaleY) + 'px';
        lbBox.style.display = 'block';
        
        // Copy tooltip content
        const tooltip = box.querySelector('.locator-tooltip');
        if (tooltip && lbTooltip) {
          lbTooltip.innerHTML = tooltip.innerHTML;
        }
      };
    }

    function closeLightbox() {
      document.getElementById('lightbox').classList.remove('active');
    }

    function shareIssue(btn) {
      const card = btn.closest('.violation-card');
      const title = card.querySelector('.violation-title').innerText;
      const wcag = card.querySelector('.badge-wcag').innerText;
      const selector = btn.getAttribute('data-selector');
      const text = `Accessibility Issue: ${title}\nWCAG Rule: ${wcag}\nSelector: ${selector}\n\nPlease review the report for AI remediation suggestions.`;
      
      navigator.clipboard.writeText(text).then(() => {
        const originalText = btn.innerHTML;
        btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: middle; margin-right: 4px;"><polyline points="20 6 9 17 4 12"></polyline></svg> Copied!`;
        btn.classList.add('copied');
        setTimeout(() => {
          btn.innerHTML = originalText;
          btn.classList.remove('copied');
        }, 2000);
      });
    }

    function copySelector(btn) {
      const selector = btn.getAttribute('data-selector');
      navigator.clipboard.writeText(selector).then(() => {
        const originalText = btn.innerText;
        btn.innerText = 'Copied!';
        btn.classList.add('copied');
        setTimeout(() => {
          btn.innerText = originalText;
          btn.classList.remove('copied');
        }, 2000);
      });
    }

    // Filtering Logic
    let currentStatusFilter = 'all';
    
    function setStatusFilter(status, cardElement) {
      // Toggle logic
      if (currentStatusFilter === status) {
        currentStatusFilter = 'all';
        document.querySelectorAll('.stat').forEach(el => el.classList.remove('active-filter'));
      } else {
        currentStatusFilter = status;
        document.querySelectorAll('.stat').forEach(el => el.classList.remove('active-filter'));
        cardElement.classList.add('active-filter');
      }
      applyFilters();
    }

    function applyFilters() {
      const wcagFilter = document.getElementById('wcagFilter').value;
      const severityFilter = document.getElementById('severityFilter').value;
      const cards = document.querySelectorAll('.violation-card');
      let visibleCount = 0;

      cards.forEach(card => {
        const status = card.getAttribute('data-status');
        const wcag = card.getAttribute('data-wcag');
        const severity = card.getAttribute('data-severity');

        const matchStatus = (currentStatusFilter === 'all' || status === currentStatusFilter);
        const matchWcag = (wcagFilter === 'all' || wcag === wcagFilter);
        const matchSeverity = (severityFilter === 'all' || severity === severityFilter);

        if (matchStatus && matchWcag && matchSeverity) {
          card.style.display = 'grid';
          visibleCount++;
        } else {
          card.style.display = 'none';
        }
      });

      const filterMsg = document.getElementById('filter-msg');
      filterMsg.innerText = `Showing ${visibleCount} of ${cards.length} issues.`;
      filterMsg.style.display = 'block';
    }

    // Scroll to Top
    window.addEventListener('scroll', () => {
      const btn = document.getElementById('scrollTopBtn');
      if (window.scrollY > 300) {
        btn.classList.add('visible');
      } else {
        btn.classList.remove('visible');
      }
    });

    function scrollToTop() {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  </script>
</head>
<body>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">

<header class="hero">
  <h1>AI Accessibility Scanner</h1>
  <div class="subtitle">
    <span><b>URL:</b> {{ result.url }}</span>
    <span><b>Browser:</b> {{ result.browser }}</span>
    <span><b>Resolution:</b> {{ result.viewport }}</span>
    <span><b>Run ID:</b> {{ result.run_id }}</span>
  </div>
</header>

<main class="container">

  <!-- Interactive Stat Filters -->
  <div class="stats-grid">
    <div class="stat" onclick="setStatusFilter('confirmed', this)">
      <div class="number" style="color:#ef4444;">{{ m.confirmed_findings }}</div>
      <div class="label">Confirmed Bugs</div>
    </div>
    <div class="stat" onclick="setStatusFilter('likely', this)">
      <div class="number" style="color:#f97316;">{{ m.likely_findings }}</div>
      <div class="label">Likely Bugs</div>
    </div>
    <div class="stat" onclick="setStatusFilter('requires_manual_review', this)">
      <div class="number" style="color:#eab308;">{{ m.manual_review_findings }}</div>
      <div class="label">Needs Review</div>
    </div>
    <div class="stat" onclick="setStatusFilter('pass', this)">
      <div class="number" style="color:#10b981;">{{ m.passed_checks }}</div>
      <div class="label">Passed Checks</div>
    </div>
  </div>

  <!-- AI Test Plan (only shown when agentic scan was run) -->
  {% if result.scan_plan %}
  <details class="test-plan-section" open>
    <summary>
      <span style="font-size:1.1rem; font-weight:700; color:#818cf8;">🤖 AI Test Plan</span>
      <span style="color:#94a3b8; font-size:0.85rem; margin-left:12px;">{{ result.scan_plan | length }} planned steps generated before scanning</span>
    </summary>
    <div class="test-plan-body">
      <p style="color:#94a3b8; font-size:0.85rem; margin-bottom:16px;">The AI agent analyzed the page's accessibility tree and generated this test plan before clicking anything. This shows exactly what the agent decided to test and why.</p>
      <div class="test-plan-steps">
        {% for step in result.scan_plan %}
        <div class="test-plan-step">
          <div class="step-number">{{ step.step }}</div>
          <div class="step-body">
            <div class="step-action">{{ step.action | upper }}</div>
            <div class="step-target">{{ step.target }}</div>
            <div class="step-reason">{{ step.reason }}</div>
            {% if step.wcag_criteria %}
            <div class="step-wcag">
              {% for crit in step.wcag_criteria %}
              <span class="severity-badge badge-wcag">WCAG {{ crit }}</span>
              {% endfor %}
            </div>
            {% endif %}
          </div>
        </div>
        {% endfor %}
      </div>
    </div>
  </details>
  {% endif %}

  <!-- Dropdown Filters -->
  <div class="filter-bar">
    <select id="wcagFilter" onchange="applyFilters()">
      <option value="all">All WCAG Criteria</option>
      {% for crit in m.wcag_criteria_affected | sort %}
      <option value="{{ crit }}">WCAG {{ crit }}</option>
      {% endfor %}
    </select>
    
    <select id="severityFilter" onchange="applyFilters()">
      <option value="all">All Severities</option>
      <option value="critical">Critical</option>
      <option value="serious">Serious</option>
      <option value="moderate">Moderate</option>
      <option value="minor">Minor</option>
    </select>
    
    <div id="filter-msg" class="filter-status-text"></div>
  </div>

  <div class="violations-list">
    {% set active_findings = findings | selectattr('duplicate_of', 'none') | list %}
    {% if not active_findings %}
      <div style="text-align:center; padding:60px; color:#10b981; font-size:1.2rem; font-weight:600; background:var(--surface); border-radius:16px;">
        ✅ No unique findings. Excellent work!
      </div>
    {% endif %}

    {% for finding in active_findings %}
    
    {% set sev_class = 'badge-moderate' %}
    {% set sev_text = 'MODERATE' %}
    {% if finding.impact %}
      {% if finding.impact.value == 'critical' %}{% set sev_class = 'badge-critical' %}{% set sev_text = 'CRITICAL' %}
      {% elif finding.impact.value == 'serious' %}{% set sev_class = 'badge-serious' %}{% set sev_text = 'SERIOUS' %}
      {% elif finding.impact.value == 'minor' %}{% set sev_class = 'badge-minor' %}{% set sev_text = 'MINOR' %}
      {% endif %}
    {% else %}
      {% if finding.status.value == 'confirmed' %}{% set sev_class = 'badge-critical' %}{% set sev_text = 'CRITICAL' %}{% endif %}
    {% endif %}

    <div class="violation-card" 
         data-status="{{ finding.status.value }}" 
         data-wcag="{{ finding.wcag.success_criterion }}" 
         data-severity="{{ (finding.impact.value if finding.impact else 'moderate') | lower }}">
      
      <!-- Screenshot -->
      <div class="card-screenshot">
        {% set ns = namespace(has_shot=false) %}
        {% for ev in finding.evidence %}
          {% if ev.evidence_type == 'screenshot' %}
            {% if ev.data %}
              {% set ns.has_shot = true %}
              <div class="screenshot-container">
                <img src="data:image/png;base64,{{ ev.data }}" alt="Page screenshot showing the accessibility issue" loading="lazy" class="full-page-img" onclick="openLightbox(this)">
                {% if finding.element.bounding_box %}
                <div class="locator-box" 
                     data-x="{{ finding.element.bounding_box.x }}" 
                     data-y="{{ finding.element.bounding_box.y }}" 
                     data-w="{{ finding.element.bounding_box.width }}" 
                     data-h="{{ finding.element.bounding_box.height }}">
                  <div class="locator-tooltip">
                    <b>{{ finding.element.selector }}</b>
                    <div class="tt-row">
                      <span>Dimensions</span>
                      <span>{{ finding.element.bounding_box.width | round | int }} x {{ finding.element.bounding_box.height | round | int }}</span>
                    </div>
                  </div>
                </div>
                {% endif %}
              </div>
            {% elif ev.screenshot_ref_id and ev.screenshot_ref_id in result.shared_screenshots %}
              {% set ns.has_shot = true %}
              <div class="screenshot-container">
                <img data-ref-id="{{ ev.screenshot_ref_id }}" alt="Page screenshot showing the accessibility issue" loading="lazy" class="full-page-img" onclick="openLightbox(this)">
                {% if finding.element.bounding_box %}
                <div class="locator-box" 
                     data-x="{{ finding.element.bounding_box.x }}" 
                     data-y="{{ finding.element.bounding_box.y }}" 
                     data-w="{{ finding.element.bounding_box.width }}" 
                     data-h="{{ finding.element.bounding_box.height }}">
                  <div class="locator-tooltip">{{ finding.element.selector }}<br>Dimensions: {{ finding.element.bounding_box.width | round }}x{{ finding.element.bounding_box.height | round }}</div>
                </div>
                {% endif %}
              </div>
            {% endif %}
            {% if ns.has_shot and finding.element.bounding_box %}
            <div class="button-group">
              <button class="btn-locate" onclick="locateElement(this)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: middle; margin-right: 4px;"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="3"></circle></svg>
                Locate Element
              </button>
            </div>
            {% endif %}
          {% endif %}
        {% endfor %}
        {% if not ns.has_shot %}
        <div class="no-screenshot">Screenshot not captured</div>
        {% endif %}
      </div>
      
      <!-- Details -->
      <div class="card-details">
        <div class="badge-row">
          {% if finding.status.value == 'confirmed' %}
          <span class="severity-badge" style="background:#7f1d1d; color:#fca5a5; border-color:#991b1b;">CONFIRMED BUG</span>
          {% elif finding.status.value == 'likely' %}
          <span class="severity-badge" style="background:#7c2d12; color:#fdba74; border-color:#9a3412;">LIKELY BUG</span>
          {% elif finding.status.value == 'requires_manual_review' %}
          <span class="severity-badge" style="background:#713f12; color:#fde047; border-color:#854d0e;">NEEDS REVIEW</span>
          {% endif %}
          
          <span class="severity-badge {{ sev_class }}">{{ sev_text }}</span>
          <span class="severity-badge badge-wcag">{{ finding.wcag.success_criterion }} {{ finding.wcag.title | upper }}</span>
        </div>
        
        <div class="violation-title">{{ finding.description }}</div>
        
        <div class="button-group" style="margin-bottom: 12px; margin-top: 4px;">
           <button class="btn-secondary" onclick="shareIssue(this)">
             <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"></path><polyline points="16 6 12 2 8 6"></polyline><line x1="12" y1="2" x2="12" y2="15"></line></svg>
             Share Issue
           </button>
        </div>
        
        <div class="explanation-block">
          <h4>Axe-Core / WCAG Validation</h4>
          <p><strong>Observed:</strong> {{ finding.actual_result }}</p>
          <p><strong>Requirement:</strong> {{ finding.expected_result }}</p>
        </div>

        <div class="xpath-container">
          <span>Selector:</span>
          <code>{{ finding.element.selector }}</code>
          <button class="copy-btn" data-selector="{{ finding.element.selector }}" onclick="copySelector(this)">Copy</button>
        </div>

        {% if finding.remediation and finding.remediation.html_fix %}
        {% set uuid = finding.finding_id %}
        <div class="fix-block">
          <h4>AI Remediation Code</h4>
          {% if finding.remediation.summary %}
          <p style="color:#34d399; font-size:0.9rem; margin-bottom:12px; font-weight:600;">{{ finding.remediation.summary }}</p>
          {% endif %}
          
          {% if finding.remediation.contradiction_warnings %}
          <div class="warning-card">
            <h4>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                <line x1="12" y1="9" x2="12" y2="13"></line>
                <line x1="12" y1="17" x2="12.01" y2="17"></line>
              </svg>
              RAG Contradiction Warning
            </h4>
            <p style="margin: 0 0 8px 0; color: #92400e; font-size: 0.9rem;">The proposed fix above may introduce new violations according to the WCAG Knowledge Base:</p>
            <ul>
              {% for warning in finding.remediation.contradiction_warnings %}
              <li>{{ warning }}</li>
              {% endfor %}
            </ul>
          </div>
          {% endif %}
          
          <div class="code-tabs">
            <button class="code-tab active" onclick="switchTab(this, 'broken-{{ uuid }}', 'fix-{{ uuid }}')">Broken Code</button>
            <button class="code-tab" onclick="switchTab(this, 'fix-{{ uuid }}', 'broken-{{ uuid }}')">Fixed Code</button>
          </div>
          
          <div class="code-block" id="broken-{{ uuid }}">
            <pre>{{ finding.element.html | e }}</pre>
          </div>
          
          <div class="code-block" id="fix-{{ uuid }}" style="display:none">
            <pre>{{ finding.remediation.html_fix | e }}</pre>
          </div>
        </div>
        {% endif %}

        <div class="wcag-ref" style="display: flex; justify-content: space-between; align-items: center;">
          <span>Verified against WCAG {{ finding.wcag.version.value }} Standard (Level {{ finding.wcag.level.value }})</span>
          {% if finding.wcag.url %}
          <a href="{{ finding.wcag.url }}" target="_blank" style="color: #6366f1; text-decoration: none; font-weight: 500; font-size: 0.9rem;">
            Read Official W3C Spec ↗
          </a>
          {% endif %}
        </div>
      </div>
    </div>
    {% endfor %}
  </div>

  <footer class="footer">
    <p>Generated by <strong>AI Accessibility Testing Agent</strong> · {{ generated_at }} UTC</p>
    <p style="margin-top:0.5rem">WCAG Ruleset validated via axe-core industry standard engine.</p>
  </footer>
</main>

<!-- Lightbox -->
<div id="lightbox" class="lightbox" onclick="if(event.target===this) closeLightbox()">
  <span class="lightbox-close" onclick="closeLightbox()">&times;</span>
  <div class="lightbox-content-wrapper">
    <img id="lb-img" class="lightbox-img" src="">
    <div id="lb-box" class="locator-box" style="display:none">
      <div id="lb-tooltip" class="locator-tooltip"></div>
    </div>
  </div>
</div>

<button id="scrollTopBtn" class="scroll-top" onclick="scrollToTop()" aria-label="Scroll to top">↑</button>

<script>
const SHARED_SCREENSHOTS = {
  {% for ref_id, b64 in result.shared_screenshots.items() %}
  "{{ ref_id }}": "data:image/png;base64,{{ b64 }}",
  {% endfor %}
};

document.querySelectorAll('img[data-ref-id]').forEach(img => {
  const refId = img.getAttribute('data-ref-id');
  if (SHARED_SCREENSHOTS[refId]) {
    img.src = SHARED_SCREENSHOTS[refId];
  }
});
</script>
</body>
</html>"""


class ReportGenerator:
    """
    Generates scan reports in JSON, HTML, and CSV formats.

    Input: A validated ScanResult object.
    Output: Report files written to the configured report directory.
    """

    def __init__(self, output_dir: Path | None = None) -> None:
        self._output_dir = output_dir or settings.report_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._env = Environment(
            loader=DictLoader({"report.html": HTML_TEMPLATE}),
            autoescape=select_autoescape(["html"]),
        )

    def generate(self, result: ScanResult) -> dict[str, Path]:
        """
        Generate all configured report formats.

        Returns a dict mapping format name to output file path.
        """
        result.finalize()
        paths: dict[str, Path] = {}

        formats = settings.report_formats

        if "json" in formats:
            paths["json"] = self._write_json(result)
        if "html" in formats:
            paths["html"] = self._write_html(result)
        if "csv" in formats:
            paths["csv"] = self._write_csv(result)

        log.info("report.generated", run_id=result.run_id, paths={k: str(v) for k, v in paths.items()})
        return paths

    def _write_json(self, result: ScanResult) -> Path:
        """Write machine-readable JSON report."""
        path = self._output_dir / f"{result.run_id}_report.json"
        data = result.model_dump(mode="json")
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        log.info("report.json_written", path=str(path))
        return path

    def _write_html(self, result: ScanResult) -> Path:
        """Write human-readable HTML report."""
        path = self._output_dir / f"{result.run_id}_report.html"

        template = self._env.get_template("report.html")
        html = template.render(
            result=result,
            findings=result.findings,
            m=result.metrics,
            version="0.1.0",
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )
        path.write_text(html, encoding="utf-8")
        log.info("report.html_written", path=str(path))
        return path

    def _write_csv(self, result: ScanResult) -> Path:
        """Write CSV report for spreadsheet-based review workflows."""
        path = self._output_dir / f"{result.run_id}_findings.csv"

        columns = [
            "finding_id", "status", "wcag_version", "wcag_sc", "wcag_title",
            "wcag_level", "detection_method", "confidence", "url", "selector",
            "description", "actual_result", "expected_result", "root_cause",
            "manual_review_required", "duplicate_of",
        ]

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for finding in result.findings:
                writer.writerow({
                    "finding_id": finding.finding_id,
                    "status": finding.status.value,
                    "wcag_version": finding.wcag.version.value,
                    "wcag_sc": finding.wcag.success_criterion,
                    "wcag_title": finding.wcag.title,
                    "wcag_level": finding.wcag.level.value,
                    "detection_method": finding.detection_method.value,
                    "confidence": finding.confidence,
                    "url": finding.url,
                    "selector": finding.element.selector,
                    "description": finding.description,
                    "actual_result": finding.actual_result,
                    "expected_result": finding.expected_result,
                    "root_cause": finding.root_cause,
                    "manual_review_required": finding.manual_review_required,
                    "duplicate_of": finding.duplicate_of or "",
                })

        log.info("report.csv_written", path=str(path), rows=len(result.findings))
        return path
