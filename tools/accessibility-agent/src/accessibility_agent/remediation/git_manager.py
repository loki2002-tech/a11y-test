"""
Git Manager — branch, commit, push, and PR lifecycle for accessibility patches.

Orchestrates the full git workflow for applying a validated patch:

  create_branch()   → git checkout -b a11y/fix-{finding_id}
  apply_patch()     → write patched file to disk
  commit_patch()    → git add <file> && git commit -m "fix(a11y): ..."
  push_branch()     → git push origin <branch>
  create_pr()       → GitHub REST API POST /repos/{owner}/{repo}/pulls
  rollback()        → git checkout {base_branch} && git branch -D <branch>

DESIGN DECISIONS
----------------
- All git operations use subprocess.run() with a configurable timeout (default 30s).
- Branch names are deterministic: a11y/fix-{sanitized_finding_id}-attempt-{n}
  This allows idempotent re-runs — if the branch already exists, it is deleted
  and recreated from the latest base branch HEAD.
- Commit messages follow Conventional Commits:
    fix(a11y): [SC {wcag_sc}] {short description} — {rule_id}
- PR body is generated from the RemediationPlan and PatchValidationResult,
  including WCAG SC reference, the unified diff, and verification status.
- GITHUB_TOKEN is read from settings.github_token (already in config.py).
  If no token, PR creation is skipped and the commit+push still works.
- Color contrast (DO_NOT_AUTO_REMEDIATE) is blocked at the planner level
  and never reaches GitManager.

ROLLBACK GUARANTEE
------------------
If any step fails after branch creation, rollback() is called automatically
by the context manager. The base branch is always restored.

Usage:
    mgr = GitManager(repo_path=Path("./my-app"), base_branch="main")

    result = mgr.full_workflow(patch, plan, validation_result)
    if result.pr_url:
        print(f"PR created: {result.pr_url}")
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from accessibility_agent.logging_config import get_logger
from accessibility_agent.remediation.schemas import (
    GeneratedPatch,
    PatchValidationResult,
    RemediationPlan,
)

log = get_logger(__name__)

# Default timeout for all git subprocess calls (seconds)
_GIT_TIMEOUT = 30

# Max characters in PR title (GitHub limit: 256, we stay conservative)
_PR_TITLE_MAX = 120

# Branch name prefix — all accessibility fix branches share this prefix
_BRANCH_PREFIX = "a11y/fix"

# GitHub API base URL
_GH_API = "https://api.github.com"


@dataclass
class GitWorkflowResult:
    """
    Result of a full GitManager workflow execution.

    Fields are populated progressively — check each boolean to understand
    how far the workflow progressed before a failure.
    """

    finding_id: str
    branch_name: str = ""
    commit_sha: str = ""
    pr_url: str = ""
    pr_number: int = 0

    # Stage completion flags
    branch_created: bool = False
    patch_applied: bool = False
    committed: bool = False
    pushed: bool = False
    pr_created: bool = False
    rolled_back: bool = False

    # Error tracking
    error: str = ""
    error_stage: str = ""

    @property
    def succeeded(self) -> bool:
        """True if the workflow completed without rollback."""
        return self.committed and not self.rolled_back and not self.error


class GitManager:
    """
    Manages the git branch → commit → push → PR workflow for accessibility patches.

    Thread safety: NOT thread-safe. Create one instance per finding.
    """

    def __init__(
        self,
        repo_path: Path,
        base_branch: str = "main",
        github_token: str = "",
        remote: str = "origin",
        dry_run: bool = False,
    ) -> None:
        self._repo = repo_path.resolve()
        self._base_branch = base_branch
        self._token = github_token
        self._remote = remote
        self._dry_run = dry_run
        self._current_branch: str = ""
        self._owner_repo: str = self._detect_owner_repo()

    # ── Public API ────────────────────────────────────────────────────────────

    def full_workflow(
        self,
        patch: GeneratedPatch,
        plan: RemediationPlan,
        validation_result: PatchValidationResult,
    ) -> GitWorkflowResult:
        """
        Execute the full git workflow: branch → apply → commit → push → PR.

        Automatically rolls back on any failure after branch creation.

        Returns a GitWorkflowResult with all stage flags and the PR URL if created.
        """
        result = GitWorkflowResult(finding_id=patch.finding_id)

        # Step 1: Create branch
        branch_name = self._make_branch_name(patch.finding_id, patch.attempt_number)
        result.branch_name = branch_name

        if not self._create_branch(branch_name, result):
            return result

        try:
            # Step 2: Apply patch to disk
            if not self._apply_patch_to_disk(patch, result):
                self._rollback(branch_name, result)
                return result

            # Step 3: Commit
            if not self._commit(patch, plan, result):
                self._rollback(branch_name, result)
                return result

            # Step 4: Push
            if not self._push(branch_name, result):
                self._rollback(branch_name, result)
                return result

            # Step 5: Create PR (optional — no rollback if this fails)
            self._create_pr(branch_name, patch, plan, validation_result, result)

        except Exception as exc:
            log.error(
                "git_manager.unexpected_error",
                finding_id=patch.finding_id,
                error=str(exc),
            )
            result.error = str(exc)
            result.error_stage = "unexpected"
            self._rollback(branch_name, result)

        return result

    def get_current_branch(self) -> str:
        """Return the current git branch name."""
        out = self._git(["rev-parse", "--abbrev-ref", "HEAD"])
        return out.strip() if out else ""

    def branch_exists(self, branch_name: str) -> bool:
        """Check whether a local branch exists."""
        out = self._git(["branch", "--list", branch_name])
        return bool(out and out.strip())

    def remote_branch_exists(self, branch_name: str) -> bool:
        """Check whether a remote branch exists."""
        out = self._git(["ls-remote", "--heads", self._remote, branch_name])
        return bool(out and out.strip())

    def rollback(self, branch_name: str) -> bool:
        """Public rollback for external callers (e.g. RemediationAgent on timeout)."""
        dummy = GitWorkflowResult(finding_id="external")
        return self._rollback(branch_name, dummy)

    # ── Private: git workflow steps ───────────────────────────────────────────

    def _create_branch(self, branch_name: str, result: GitWorkflowResult) -> bool:
        """Create (or recreate) the fix branch from base_branch HEAD."""
        if self._dry_run:
            log.info("git_manager.dry_run.create_branch", branch=branch_name)
            result.branch_created = True
            self._current_branch = branch_name
            return True

        # First, ensure we're on the base branch with a clean checkout
        checkout_base = self._git(["checkout", self._base_branch])
        if checkout_base is None:
            result.error = f"Failed to checkout base branch '{self._base_branch}'"
            result.error_stage = "create_branch"
            return False

        # Pull latest from remote (best-effort — don't fail if offline)
        self._git(["pull", self._remote, self._base_branch], check=False)

        # Delete existing branch if it exists (idempotent re-runs)
        if self.branch_exists(branch_name):
            log.info("git_manager.deleting_existing_branch", branch=branch_name)
            self._git(["branch", "-D", branch_name], check=False)

        # Create and checkout the new branch
        out = self._git(["checkout", "-b", branch_name])
        if out is None:
            result.error = f"Failed to create branch '{branch_name}'"
            result.error_stage = "create_branch"
            return False

        self._current_branch = branch_name
        result.branch_created = True
        log.info("git_manager.branch_created", branch=branch_name)
        return True

    def _apply_patch_to_disk(
        self, patch: GeneratedPatch, result: GitWorkflowResult
    ) -> bool:
        """Write the patched file content to disk."""
        if self._dry_run:
            log.info("git_manager.dry_run.apply_patch", file=patch.target_file)
            result.patch_applied = True
            return True

        from accessibility_agent.remediation.patcher import PatchGenerator

        generator = PatchGenerator(repo_path=self._repo)
        success = generator.apply_patch_to_file(patch, dry_run=False)

        if not success:
            result.error = f"Failed to apply patch to {patch.target_file}"
            result.error_stage = "apply_patch"
            return False

        result.patch_applied = True
        log.info("git_manager.patch_applied", file=patch.target_file)
        return True

    def _commit(
        self,
        patch: GeneratedPatch,
        plan: RemediationPlan,
        result: GitWorkflowResult,
    ) -> bool:
        """Stage the patched file and create a commit."""
        if self._dry_run:
            log.info("git_manager.dry_run.commit", file=patch.target_file)
            result.committed = True
            result.commit_sha = "dry-run-sha"
            return True

        # Stage only the target file (never `git add .`)
        add_out = self._git(["add", patch.target_file])
        if add_out is None:
            result.error = f"git add {patch.target_file} failed"
            result.error_stage = "commit"
            return False

        # Build commit message
        commit_msg = self._build_commit_message(patch, plan)

        commit_out = self._git(["commit", "-m", commit_msg])
        if commit_out is None:
            result.error = "git commit failed"
            result.error_stage = "commit"
            return False

        # Extract commit SHA
        sha_out = self._git(["rev-parse", "HEAD"])
        result.commit_sha = sha_out.strip() if sha_out else ""
        result.committed = True

        log.info(
            "git_manager.committed",
            branch=self._current_branch,
            sha=result.commit_sha[:8],
            file=patch.target_file,
        )
        return True

    def _push(self, branch_name: str, result: GitWorkflowResult) -> bool:
        """Push the branch to the remote."""
        if self._dry_run:
            log.info("git_manager.dry_run.push", branch=branch_name)
            result.pushed = True
            return True

        push_out = self._git(
            ["push", self._remote, branch_name, "--force"]
        )
        if push_out is None:
            result.error = f"git push to '{branch_name}' failed"
            result.error_stage = "push"
            return False

        result.pushed = True
        log.info("git_manager.pushed", branch=branch_name, remote=self._remote)
        return True

    def _create_pr(
        self,
        branch_name: str,
        patch: GeneratedPatch,
        plan: RemediationPlan,
        validation_result: PatchValidationResult,
        result: GitWorkflowResult,
    ) -> None:
        """Create a GitHub Pull Request. Non-fatal if it fails."""
        if not self._token:
            log.info("git_manager.pr_skipped_no_token", branch=branch_name)
            return

        if not self._owner_repo:
            log.warning("git_manager.pr_skipped_no_remote", branch=branch_name)
            return

        if self._dry_run:
            log.info("git_manager.dry_run.create_pr", branch=branch_name)
            result.pr_url = f"https://github.com/{self._owner_repo}/pull/dry-run"
            result.pr_created = True
            return

        title = self._build_pr_title(plan)
        body = self._build_pr_body(patch, plan, validation_result)

        payload = {
            "title": title,
            "body": body,
            "head": branch_name,
            "base": self._base_branch,
            "draft": False,
        }

        url = f"{_GH_API}/repos/{self._owner_repo}/pulls"

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": f"token {self._token}",
                    "Accept": "application/vnd.github.v3+json",
                    "Content-Type": "application/json",
                    "User-Agent": "ai-accessibility-agent/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))

            result.pr_url = resp_data.get("html_url", "")
            result.pr_number = resp_data.get("number", 0)
            result.pr_created = True

            log.info(
                "git_manager.pr_created",
                pr_url=result.pr_url,
                pr_number=result.pr_number,
                branch=branch_name,
            )

        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")[:300]
            # Ignore "pull request already exists" (422) error
            if exc.code == 422 and "already exists" in error_body:
                log.info(
                    "git_manager.pr_already_exists",
                    branch=branch_name,
                )
                result.pr_created = True
                result.pr_url = f"https://github.com/{self._owner_repo}/pulls"
            else:
                log.warning(
                    "git_manager.pr_failed",
                    status=exc.code,
                    error=error_body,
                    branch=branch_name,
                )
                result.error = f"PR creation failed: HTTP {exc.code} — {error_body}"
                # NOT setting error_stage — PR failure doesn't trigger rollback

        except Exception as exc:
            log.warning(
                "git_manager.pr_failed",
                error=str(exc),
                branch=branch_name,
            )
            result.error = f"PR creation failed: {exc}"

    def _rollback(self, branch_name: str, result: GitWorkflowResult) -> bool:
        """Checkout base branch and delete the fix branch."""
        if self._dry_run:
            log.info("git_manager.dry_run.rollback", branch=branch_name)
            result.rolled_back = True
            return True

        log.warning(
            "git_manager.rollback",
            branch=branch_name,
            failed_stage=result.error_stage,
        )

        # Return to base branch
        self._git(["checkout", self._base_branch], check=False)

        # Delete the fix branch (local)
        if self.branch_exists(branch_name):
            self._git(["branch", "-D", branch_name], check=False)

        # Delete remote branch if it was pushed
        if result.pushed and self.remote_branch_exists(branch_name):
            self._git(
                ["push", self._remote, "--delete", branch_name], check=False
            )

        result.rolled_back = True
        log.info("git_manager.rollback_complete", branch=branch_name)
        return True

    # ── Git subprocess wrapper ────────────────────────────────────────────────

    def _git(
        self,
        args: list[str],
        check: bool = True,
        timeout: int = _GIT_TIMEOUT,
    ) -> str | None:
        """
        Run a git command in the repo directory.

        Returns stdout as a string on success.
        Returns None on failure (if check=True, logs the error).
        Returns "" on success with no stdout.
        """
        cmd = ["git"] + args
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(self._repo),
                timeout=timeout,
            )
            if proc.returncode != 0:
                if check:
                    log.error(
                        "git_manager.git_error",
                        cmd=" ".join(args[:3]),
                        returncode=proc.returncode,
                        stderr=proc.stderr.strip()[:200],
                    )
                return None
            return proc.stdout

        except subprocess.TimeoutExpired:
            log.error("git_manager.git_timeout", cmd=" ".join(args[:3]), timeout=timeout)
            return None
        except FileNotFoundError:
            log.error("git_manager.git_not_found", cmd=args[0])
            return None
        except Exception as exc:
            log.error("git_manager.git_exception", error=str(exc))
            return None

    # ── String builders ───────────────────────────────────────────────────────

    @staticmethod
    def _make_branch_name(finding_id: str, attempt: int) -> str:
        """
        Build a deterministic, git-safe branch name.

        Format: a11y/fix-{sanitized_finding_id}-attempt-{n}

        finding_id like 'A11Y-button-name-0x3f' → 'a11y/fix-a11y-button-name-0x3f-attempt-1'
        """
        # Sanitize: lowercase, replace non-alphanumeric with -, strip leading/trailing -
        safe_id = re.sub(r"[^a-zA-Z0-9]+", "-", finding_id).strip("-").lower()
        # Truncate to keep branch names under 80 chars
        safe_id = safe_id[:50]
        return f"{_BRANCH_PREFIX}-{safe_id}-attempt-{attempt}"

    @staticmethod
    def _build_commit_message(patch: GeneratedPatch, plan: RemediationPlan) -> str:
        """
        Build a conventional-commit message for the accessibility fix.

        Format:
            fix(a11y): [SC {wcag_sc}] {short description}

            - Finding: {finding_id}
            - File: {target_file} (+{added}/-{removed} lines)
            - Rule: {rule_id}
            - WCAG: {wcag_sc} — {fix_strategy_first_sentence}
            - Patch: {patch_id}
            - Attempt: {attempt}

            Auto-generated by AI Accessibility Remediation Agent.
            Verify with: python -m accessibility_agent scan --url <url>
        """
        sc = plan.wcag_criterion or "Unknown"
        # First sentence of fix_strategy for the title
        first_sentence = plan.fix_strategy.split(".")[0].strip()
        if len(first_sentence) > 72:
            first_sentence = first_sentence[:69] + "..."

        title = f"fix(a11y): [SC {sc}] {first_sentence}"

        body_lines = [
            "",
            f"- Finding:  {patch.finding_id}",
            f"- File:     {patch.target_file} (+{patch.lines_added}/-{patch.lines_removed} lines)",
            f"- WCAG SC:  {sc}",
            f"- Strategy: {plan.fix_strategy[:120]}",
            f"- Patch ID: {patch.patch_id}",
            f"- Attempt:  {patch.attempt_number}",
            "",
            "Auto-generated by AI Accessibility Remediation Agent.",
            "Verify with: python -m accessibility_agent scan --url <url>",
        ]

        return title + "\n" + "\n".join(body_lines)

    @staticmethod
    def _build_pr_title(plan: RemediationPlan) -> str:
        """Build a concise PR title."""
        sc = plan.wcag_criterion or "Unknown SC"
        strategy_short = plan.fix_strategy.split(".")[0].strip()
        title = f"fix(a11y): [WCAG {sc}] {strategy_short}"
        return title[:_PR_TITLE_MAX]

    @staticmethod
    def _build_pr_body(
        patch: GeneratedPatch,
        plan: RemediationPlan,
        validation_result: PatchValidationResult,
    ) -> str:
        """Build a rich PR description for human review."""
        sc = plan.wcag_criterion or "Unknown"

        gates_summary = []
        for gate, label in [
            (validation_result.file_exists, "File exists"),
            (validation_result.context_matches, "Context matches"),
            (validation_result.applies_cleanly, "Applies cleanly"),
            (validation_result.no_unrelated_changes, "No unrelated changes"),
            (validation_result.no_secrets_detected, "No secrets"),
            (validation_result.no_invalid_aria, "No invalid ARIA"),
            (validation_result.no_new_contradictions, "No new contradictions"),
        ]:
            icon = "✅" if gate else "❌"
            gates_summary.append(f"  - {icon} {label}")

        gates_text = "\n".join(gates_summary)

        # Truncate diff for PR body (GitHub limit: 65536 chars)
        diff_preview = patch.unified_diff[:3000]
        if len(patch.unified_diff) > 3000:
            diff_preview += "\n... (truncated)"

        contradictions_text = ""
        if validation_result.contradiction_details:
            warnings = [
                c for c in validation_result.contradiction_details
                if c.get("is_blocking") != "true"
            ]
            if warnings:
                warn_lines = "\n".join(
                    f"  - ⚠️ SC {w['violates_sc']}: {w['message'][:100]}"
                    for w in warnings[:3]
                )
                contradictions_text = f"\n### ⚠️ Non-blocking Warnings\n{warn_lines}\n"

        return f"""## ♿ Accessibility Fix — {sc}

> **Auto-generated by the AI Accessibility Remediation Agent.**
> Human review is required before merging.

### Summary
{plan.fix_strategy}

### Finding Details
| Field | Value |
|-------|-------|
| Finding ID | `{patch.finding_id}` |
| WCAG Success Criterion | {sc} |
| Target File | `{patch.target_file}` |
| Lines Changed | +{patch.lines_added} / -{patch.lines_removed} |
| Attempt | {patch.attempt_number} |
| Patch ID | `{patch.patch_id}` |

### Root Cause
{plan.root_cause}

### Validation Gates
All 7 safety gates passed before this PR was created:
{gates_text}
{contradictions_text}
### Diff
```diff
{diff_preview}
```

### Verification
After merging, verify with:
```bash
python -m accessibility_agent scan --url <your-staging-url>
```

---
*This PR was created by the AI Accessibility Remediation Agent.*
*Always verify accessibility fixes with real assistive technology.*
"""

    def _detect_owner_repo(self) -> str:
        """
        Detect the GitHub owner/repo from the git remote URL.

        Handles:
          - HTTPS: https://github.com/owner/repo.git
          - SSH:   git@github.com:owner/repo.git
        """
        try:
            out = self._git(
                ["remote", "get-url", "origin"], check=False
            )
            if not out:
                return ""
            url = out.strip()

            # HTTPS format
            m = re.search(r"github\.com[/:]([^/]+/[^/\.]+?)(?:\.git)?$", url)
            if m:
                return m.group(1)
        except Exception:
            pass
        return ""
