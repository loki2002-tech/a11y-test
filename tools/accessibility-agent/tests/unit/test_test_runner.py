"""
Unit tests for Phase 6: TestRunner (test_runner.py)

Groups:
1. Framework detection based on file presence.
2. Command construction per framework.
3. Test execution simulation (subprocess mocking).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from accessibility_agent.remediation.test_runner import (
    TestRunner,
    TestFramework,
    TestRunResult,
    _TEST_TIMEOUT
)

@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    return tmp_path

# ═════════════════════════════════════════════════════════════════════════════
# GROUP 1 — Framework Detection
# ═════════════════════════════════════════════════════════════════════════════

class TestFrameworkDetection:
    
    def test_detect_playwright(self, tmp_repo: Path):
        (tmp_repo / "playwright.config.ts").touch()
        runner = TestRunner(tmp_repo)
        assert runner.detect_framework() == TestFramework.PLAYWRIGHT

    def test_detect_pytest(self, tmp_repo: Path):
        (tmp_repo / "pytest.ini").touch()
        runner = TestRunner(tmp_repo)
        assert runner.detect_framework() == TestFramework.PYTEST

    def test_detect_pytest_by_tests_dir(self, tmp_repo: Path):
        tests_dir = tmp_repo / "tests" / "unit"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_example.py").touch()
        runner = TestRunner(tmp_repo)
        assert runner.detect_framework() == TestFramework.PYTEST

    def test_detect_vitest(self, tmp_repo: Path):
        (tmp_repo / "vitest.config.ts").touch()
        runner = TestRunner(tmp_repo)
        assert runner.detect_framework() == TestFramework.VITEST

    def test_detect_jest(self, tmp_repo: Path):
        (tmp_repo / "jest.config.js").touch()
        runner = TestRunner(tmp_repo)
        assert runner.detect_framework() == TestFramework.JEST

    def test_detect_npm_test(self, tmp_repo: Path):
        pkg_json = tmp_repo / "package.json"
        pkg_json.write_text(json.dumps({"scripts": {"test": "mocha"}}), encoding="utf-8")
        runner = TestRunner(tmp_repo)
        assert runner.detect_framework() == TestFramework.NPM_TEST

    def test_detect_npm_test_skips_echo_fallback(self, tmp_repo: Path):
        pkg_json = tmp_repo / "package.json"
        pkg_json.write_text(json.dumps({"scripts": {"test": "echo 'Error: no test specified' && exit 1"}}), encoding="utf-8")
        runner = TestRunner(tmp_repo)
        assert runner.detect_framework() == TestFramework.UNKNOWN

    def test_detect_unknown(self, tmp_repo: Path):
        runner = TestRunner(tmp_repo)
        assert runner.detect_framework() == TestFramework.UNKNOWN

# ═════════════════════════════════════════════════════════════════════════════
# GROUP 2 — Command Construction
# ═════════════════════════════════════════════════════════════════════════════

class TestCommandConstruction:

    def test_commands_by_framework(self, tmp_repo: Path):
        runner = TestRunner(tmp_repo)
        
        assert runner._get_command_for_framework(TestFramework.PYTEST) == ["python", "-m", "pytest", "-v", "--tb=short"]
        assert runner._get_command_for_framework(TestFramework.PLAYWRIGHT) == ["npx", "playwright", "test"]
        assert runner._get_command_for_framework(TestFramework.VITEST) == ["npx", "vitest", "run"]
        assert runner._get_command_for_framework(TestFramework.JEST) == ["npx", "jest", "--passWithNoTests"]
        assert runner._get_command_for_framework(TestFramework.NPM_TEST) == ["npm", "test"]
        assert runner._get_command_for_framework(TestFramework.UNKNOWN) == []


# ═════════════════════════════════════════════════════════════════════════════
# GROUP 3 — Execution Simulation
# ═════════════════════════════════════════════════════════════════════════════

class TestExecutionSimulation:

    def test_run_tests_success(self, tmp_repo: Path):
        (tmp_repo / "pytest.ini").touch()
        runner = TestRunner(tmp_repo)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "2 passed in 0.1s"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            result = runner.run_tests()
            
            assert result.ran_tests is True
            assert result.success is True
            assert result.framework == TestFramework.PYTEST
            assert result.output == "2 passed in 0.1s"
            
            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            assert args[0] == ["python", "-m", "pytest", "-v", "--tb=short"]
            assert kwargs["cwd"] == str(tmp_repo)
            assert kwargs["timeout"] == _TEST_TIMEOUT

    def test_run_tests_failure(self, tmp_repo: Path):
        (tmp_repo / "playwright.config.ts").touch()
        runner = TestRunner(tmp_repo)

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "Error: 1 test failed"

        with patch("subprocess.run", return_value=mock_proc):
            result = runner.run_tests()
            
            assert result.ran_tests is True
            assert result.success is False
            assert result.error == "Error: 1 test failed"

    def test_run_tests_timeout(self, tmp_repo: Path):
        (tmp_repo / "jest.config.js").touch()
        runner = TestRunner(tmp_repo, timeout=5)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="npx", timeout=5)):
            result = runner.run_tests()
            
            assert result.ran_tests is True
            assert result.success is False
            assert "timed out" in result.error

    def test_run_tests_skipped_when_no_framework(self, tmp_repo: Path):
        runner = TestRunner(tmp_repo)
        
        # Ensure no subprocess is called
        with patch("subprocess.run") as mock_run:
            result = runner.run_tests()
            
            assert result.ran_tests is False
            assert result.success is False
            assert result.skipped_reason == "No supported test framework detected."
            mock_run.assert_not_called()
