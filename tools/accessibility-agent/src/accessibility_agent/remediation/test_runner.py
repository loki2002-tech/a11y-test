"""
Test Runner — auto-detects and runs the project's test suite to verify patches.

After applying an accessibility patch, it's critical to ensure we haven't broken
existing functionality. The TestRunner inspects the workspace, determines the
testing framework, and runs the tests.

Features:
- Framework detection: pytest, jest, vitest, playwright, npm test
- Timeout enforcement: Prevents the agent from hanging on long suites (default 60s)
- Graceful degradation: If no tests are found, it skips cleanly.
"""

from __future__ import annotations

import json
import subprocess
from enum import Enum
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field

from accessibility_agent.logging_config import get_logger

log = get_logger(__name__)

# Default timeout for test suites (seconds).
_TEST_TIMEOUT = 60


class TestFramework(str, Enum):
    """Supported test frameworks."""
    PYTEST = "pytest"
    JEST = "jest"
    VITEST = "vitest"
    PLAYWRIGHT = "playwright"
    NPM_TEST = "npm_test"
    UNKNOWN = "unknown"


class TestRunResult(BaseModel):
    """Results of attempting to run the test suite."""
    framework: TestFramework = TestFramework.UNKNOWN
    ran_tests: bool = False
    success: bool = False
    output: str = ""
    error: str = ""
    skipped_reason: str = ""


class TestRunner:
    """
    Detects and executes the test suite for the current repository.
    """

    def __init__(self, repo_path: Path, timeout: int = _TEST_TIMEOUT) -> None:
        self._repo = repo_path.resolve()
        self._timeout = timeout

    def detect_framework(self) -> TestFramework:
        """Detect the primary test framework used in the repository."""
        # 1. Playwright
        if (self._repo / "playwright.config.ts").exists() or \
           (self._repo / "playwright.config.js").exists():
            return TestFramework.PLAYWRIGHT

        # 2. Pytest
        if (self._repo / "pytest.ini").exists() or \
           (self._repo / "conftest.py").exists() or \
           (self._repo / "tox.ini").exists() or \
           list(self._repo.glob("tests/**/test_*.py")):
            return TestFramework.PYTEST

        # 3. Vitest
        if (self._repo / "vitest.config.ts").exists() or \
           (self._repo / "vitest.config.js").exists():
            return TestFramework.VITEST

        # 4. Jest
        if (self._repo / "jest.config.js").exists() or \
           (self._repo / "jest.config.ts").exists():
            return TestFramework.JEST

        # 5. Fallback to npm test (if package.json has a test script)
        pkg_json = self._repo / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
                scripts = data.get("scripts", {})
                if "test" in scripts and "echo" not in scripts["test"]:
                    return TestFramework.NPM_TEST
            except Exception:
                pass

        return TestFramework.UNKNOWN

    def run_tests(self) -> TestRunResult:
        """
        Detect framework and run tests.
        Returns a TestRunResult indicating success, failure, or skipped.
        """
        framework = self.detect_framework()
        if framework == TestFramework.UNKNOWN:
            log.info("test_runner.skipped_no_framework", repo=str(self._repo))
            return TestRunResult(
                framework=framework,
                ran_tests=False,
                skipped_reason="No supported test framework detected."
            )

        cmd = self._get_command_for_framework(framework)
        if not cmd:
            return TestRunResult(
                framework=framework,
                ran_tests=False,
                skipped_reason=f"Could not construct command for {framework.value}"
            )

        log.info("test_runner.start", framework=framework.value, cmd=" ".join(cmd))
        result = TestRunResult(framework=framework, ran_tests=True)

        try:
            # We run the command through shell=True for npm/npx on Windows, 
            # but list of args is preferred. We'll use shell=False but resolve executables.
            proc = subprocess.run(
                cmd,
                cwd=str(self._repo),
                capture_output=True,
                text=True,
                timeout=self._timeout,
                shell=(True if "npm" in cmd[0] or "npx" in cmd[0] else False)
            )
            
            result.output = proc.stdout.strip()
            result.error = proc.stderr.strip()
            result.success = (proc.returncode == 0)

            if result.success:
                log.info("test_runner.success", framework=framework.value)
            else:
                log.warning("test_runner.failure", framework=framework.value, code=proc.returncode)

        except subprocess.TimeoutExpired:
            log.warning("test_runner.timeout", framework=framework.value, timeout=self._timeout)
            result.success = False
            result.error = f"Test suite timed out after {self._timeout} seconds."
        except FileNotFoundError as e:
            log.error("test_runner.not_found", cmd=cmd[0])
            result.success = False
            result.error = f"Test executable not found: {cmd[0]}"
        except Exception as e:
            log.error("test_runner.error", error=str(e))
            result.success = False
            result.error = f"Unexpected error running tests: {e}"

        return result

    def _get_command_for_framework(self, framework: TestFramework) -> list[str]:
        """Returns the CLI command to execute the test suite."""
        if framework == TestFramework.PYTEST:
            return ["python", "-m", "pytest", "-v", "--tb=short"]
        elif framework == TestFramework.PLAYWRIGHT:
            return ["npx", "playwright", "test"]
        elif framework == TestFramework.VITEST:
            return ["npx", "vitest", "run"]
        elif framework == TestFramework.JEST:
            return ["npx", "jest", "--passWithNoTests"]
        elif framework == TestFramework.NPM_TEST:
            return ["npm", "test"]
        return []
