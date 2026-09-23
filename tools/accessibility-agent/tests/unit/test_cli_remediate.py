"""
Unit tests for Phase 9: CLI 'remediate' subcommand.

Tests the Typer CLI wiring — input validation, flag handling, and output
formatting — using CliRunner and mocked RemediationAgent.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from accessibility_agent.cli import app
from accessibility_agent.remediation.agent import RemediationAgentResult
from accessibility_agent.remediation.schemas import RemediationStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

runner = CliRunner()


def _make_finding_json(finding_id: str = "A11Y-001") -> str:
    return json.dumps({
        "finding_id": finding_id,
        "url": "http://localhost/",
        "rule_id": "html-has-lang",
        "wcag": {
            "version": "2.2",
            "success_criterion": "3.1.1",
            "title": "Language of Page",
            "level": "A",
            "principle": "Understandable",
        },
        "status": "confirmed",
        "detection_method": "automated",
        "description": "html element has no lang attribute",
        "evidence": [{"evidence_type": "tool_output", "data": "[]", "description": "Mock"}],
    })


def _make_successful_agent_result(finding_id: str = "A11Y-001") -> RemediationAgentResult:
    r = RemediationAgentResult(
        finding_id=finding_id,
        status=RemediationStatus.VERIFIED,
        attempts=1,
        tests_ran=True,
        tests_passed=True,
        pr_url="https://github.com/owner/repo/pull/42",
        duration_seconds=3.14,
    )
    return r


def _make_failed_result(finding_id: str = "A11Y-001") -> RemediationAgentResult:
    return RemediationAgentResult(
        finding_id=finding_id,
        status=RemediationStatus.FAILED,
        attempts=3,
        failure_reason="All 3 remediation attempts failed.",
        duration_seconds=12.0,
    )


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 1 — Input validation
# ═════════════════════════════════════════════════════════════════════════════


class TestInputValidation:

    def test_no_finding_or_json_exits_with_error(self, tmp_path: Path):
        """Neither --finding nor --json provided should exit with error."""
        result = runner.invoke(app, ["remediate", "--repo", str(tmp_path)])
        assert result.exit_code == 1
        assert "provide either --finding" in result.output or "Error" in result.output

    def test_invalid_json_string_exits_with_error(self, tmp_path: Path):
        result = runner.invoke(app, [
            "remediate", "--json", "NOT_VALID_JSON", "--repo", str(tmp_path)
        ])
        assert result.exit_code == 1
        assert "Failed to parse" in result.output

    def test_nonexistent_repo_exits_with_error(self, tmp_path: Path):
        result = runner.invoke(app, [
            "remediate",
            "--json", _make_finding_json(),
            "--repo", "/definitely/does/not/exist/abc123",
        ])
        assert result.exit_code == 1
        assert "does not exist" in result.output

    def test_valid_finding_file_is_parsed(self, tmp_path: Path):
        finding_file = tmp_path / "finding.json"
        finding_file.write_text(_make_finding_json(), encoding="utf-8")

        with patch("accessibility_agent.cli.RemediationAgent") as MockAgent:
            MockAgent.return_value.remediate.return_value = _make_successful_agent_result()
            result = runner.invoke(app, [
                "remediate",
                "--finding", str(finding_file),
                "--repo", str(tmp_path),
            ])
        assert result.exit_code == 0

    def test_invalid_finding_file_exits(self, tmp_path: Path):
        bad_file = tmp_path / "finding.json"
        bad_file.write_text("NOT JSON", encoding="utf-8")
        result = runner.invoke(app, [
            "remediate", "--finding", str(bad_file), "--repo", str(tmp_path)
        ])
        assert result.exit_code == 1


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 2 — Successful remediation output
# ═════════════════════════════════════════════════════════════════════════════


class TestSuccessfulRemediation:

    def test_verified_exit_code_zero(self, tmp_path: Path):
        with patch("accessibility_agent.cli.RemediationAgent") as MockAgent:
            MockAgent.return_value.remediate.return_value = _make_successful_agent_result()
            result = runner.invoke(app, [
                "remediate", "--json", _make_finding_json(), "--repo", str(tmp_path)
            ])
        assert result.exit_code == 0

    def test_output_contains_finding_id(self, tmp_path: Path):
        with patch("accessibility_agent.cli.RemediationAgent") as MockAgent:
            MockAgent.return_value.remediate.return_value = _make_successful_agent_result()
            result = runner.invoke(app, [
                "remediate", "--json", _make_finding_json(), "--repo", str(tmp_path)
            ])
        assert "A11Y-001" in result.output

    def test_output_contains_pr_url(self, tmp_path: Path):
        with patch("accessibility_agent.cli.RemediationAgent") as MockAgent:
            MockAgent.return_value.remediate.return_value = _make_successful_agent_result()
            result = runner.invoke(app, [
                "remediate", "--json", _make_finding_json(), "--repo", str(tmp_path)
            ])
        assert "github.com" in result.output

    def test_output_contains_verified_status(self, tmp_path: Path):
        with patch("accessibility_agent.cli.RemediationAgent") as MockAgent:
            MockAgent.return_value.remediate.return_value = _make_successful_agent_result()
            result = runner.invoke(app, [
                "remediate", "--json", _make_finding_json(), "--repo", str(tmp_path)
            ])
        assert "VERIFIED" in result.output


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 3 — Failure cases
# ═════════════════════════════════════════════════════════════════════════════


class TestFailureCases:

    def test_failed_status_exits_with_code_one(self, tmp_path: Path):
        with patch("accessibility_agent.cli.RemediationAgent") as MockAgent:
            MockAgent.return_value.remediate.return_value = _make_failed_result()
            result = runner.invoke(app, [
                "remediate", "--json", _make_finding_json(), "--repo", str(tmp_path)
            ])
        assert result.exit_code == 1

    def test_manual_review_exits_zero(self, tmp_path: Path):
        """Manual review is a valid exit — not a failure that blocks CI."""
        manual_result = RemediationAgentResult(
            finding_id="A11Y-001",
            status=RemediationStatus.MANUAL_REVIEW,
            manual_review_notes="Classifier: DO_NOT_AUTO_REMEDIATE",
            attempts=1,
        )
        with patch("accessibility_agent.cli.RemediationAgent") as MockAgent:
            MockAgent.return_value.remediate.return_value = manual_result
            result = runner.invoke(app, [
                "remediate", "--json", _make_finding_json(), "--repo", str(tmp_path)
            ])
        assert result.exit_code == 0

    def test_failure_reason_in_output(self, tmp_path: Path):
        with patch("accessibility_agent.cli.RemediationAgent") as MockAgent:
            MockAgent.return_value.remediate.return_value = _make_failed_result()
            result = runner.invoke(app, [
                "remediate", "--json", _make_finding_json(), "--repo", str(tmp_path)
            ])
        assert "attempts failed" in result.output


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 4 — CLI flags
# ═════════════════════════════════════════════════════════════════════════════


class TestCLIFlags:

    def test_dry_run_flag_passed_to_agent(self, tmp_path: Path):
        with patch("accessibility_agent.cli.RemediationAgent") as MockAgent:
            MockAgent.return_value.remediate.return_value = _make_successful_agent_result()
            runner.invoke(app, [
                "remediate", "--json", _make_finding_json(),
                "--repo", str(tmp_path), "--dry-run"
            ])
            # Verify agent was instantiated with dry_run=True
            kwargs = MockAgent.call_args.kwargs
            assert kwargs.get("dry_run") is True

    def test_no_tests_flag_disables_blocking(self, tmp_path: Path):
        with patch("accessibility_agent.cli.RemediationAgent") as MockAgent:
            MockAgent.return_value.remediate.return_value = _make_successful_agent_result()
            runner.invoke(app, [
                "remediate", "--json", _make_finding_json(),
                "--repo", str(tmp_path), "--no-tests"
            ])
            kwargs = MockAgent.call_args.kwargs
            assert kwargs.get("block_on_test_failure") is False

    def test_no_pr_flag_passed_to_agent(self, tmp_path: Path):
        with patch("accessibility_agent.cli.RemediationAgent") as MockAgent:
            MockAgent.return_value.remediate.return_value = _make_successful_agent_result()
            runner.invoke(app, [
                "remediate", "--json", _make_finding_json(),
                "--repo", str(tmp_path), "--no-pr"
            ])
            kwargs = MockAgent.call_args.kwargs
            assert kwargs.get("create_pr") is False

    def test_output_flag_saves_json(self, tmp_path: Path):
        output_file = tmp_path / "result.json"
        with patch("accessibility_agent.cli.RemediationAgent") as MockAgent:
            MockAgent.return_value.remediate.return_value = _make_successful_agent_result()
            runner.invoke(app, [
                "remediate", "--json", _make_finding_json(),
                "--repo", str(tmp_path),
                "--output", str(output_file),
            ])
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data.get("finding_id") == "A11Y-001"
