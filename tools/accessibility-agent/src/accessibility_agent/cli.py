"""
CLI Entry Point — AI Accessibility Testing Agent.

Provides the ``a11y-agent`` command with subcommands:
    scan        Run an accessibility scan
    report      Re-generate a report from an existing JSON result
    version     Show version information

Usage examples:
    a11y-agent scan --url https://example.com
    a11y-agent scan --url https://example.com --mode full --output ./reports
    a11y-agent scan --url https://example.com --no-headless --browser firefox
    a11y-agent version
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from accessibility_agent.logging_config import configure_logging, get_logger
from accessibility_agent.remediation.agent import RemediationAgent
from accessibility_agent.wcag.schemas import Finding

app = typer.Typer(
    name="a11y-agent",
    help="AI-Powered Accessibility Testing Agent — deterministic-first, AI-assisted.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


@app.callback()
def _global_options() -> None:
    """Configure logging before any command runs."""
    configure_logging()


@app.command("scan")
def cmd_scan(
    url: Annotated[str, typer.Option("--url", "-u", help="Target URL to scan")],
    mode: Annotated[
        str,
        typer.Option(
            "--mode", "-m",
            help="Scan mode: automated | keyboard | semantic | visual | interaction | dynamic | manual-assist | ai-analysis | full",
        ),
    ] = "automated",
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Output directory for reports"),
    ] = None,
    browser: Annotated[
        str,
        typer.Option("--browser", "-b", help="Browser: chromium | firefox | webkit"),
    ] = "chromium",
    headless: Annotated[
        bool,
        typer.Option("--headless/--no-headless", help="Run browser in headless mode"),
    ] = True,
    viewport: Annotated[
        str,
        typer.Option("--viewport", help="Viewport as WIDTHxHEIGHT, e.g. 1280x720"),
    ] = "1280x720",
    formats: Annotated[
        str,
        typer.Option("--formats", help="Comma-separated report formats: json,html,csv"),
    ] = "json,html",
    agentic: Annotated[
        bool,
        typer.Option("--agentic", help="Enable Agentic Observe/Plan/Act loop for dynamic DOM interaction"),
    ] = False,
    # ── Authentication flags ──────────────────────────────────────────────
    auth_state: Annotated[
        Optional[Path],
        typer.Option(
            "--auth-state",
            help="Path to a Playwright storage state JSON file (cookies + localStorage). "
                 "Generate with: playwright codegen --save-storage=auth.json <url>",
        ),
    ] = None,
    login_url: Annotated[
        Optional[str],
        typer.Option(
            "--login-url",
            help="URL of the login page. Agent will auto-fill credentials before scanning.",
        ),
    ] = None,
    login_username: Annotated[
        Optional[str],
        typer.Option("--login-username", help="Username or email for auto-login."),
    ] = None,
    login_password: Annotated[
        Optional[str],
        typer.Option(
            "--login-password",
            help="Password for auto-login. Prefer setting A11Y_LOGIN_PASSWORD env var in CI.",
            envvar="A11Y_LOGIN_PASSWORD",
        ),
    ] = None,
) -> None:
    """
    Run an accessibility scan against a URL.

    Examples:

    \\b
    # Basic automated scan
    a11y-agent scan --url https://example.com

    \\b
    # Full audit in Firefox, non-headless
    a11y-agent scan --url https://example.com --mode full --browser firefox --no-headless

    \\b
    # Scan a protected page using a saved Playwright auth state
    a11y-agent scan --url https://app.example.com/dashboard --auth-state ./auth.json

    \\b
    # Scan a protected page using auto-login credentials
    a11y-agent scan --url https://app.example.com/dashboard \\
        --login-url https://app.example.com/login \\
        --login-username admin@example.com \\
        --login-password secret123
    """
    import os
    log = get_logger("cli")

    # Parse viewport
    try:
        w, h = map(int, viewport.lower().split("x"))
    except ValueError:
        err_console.print(f"[red]Invalid viewport format: {viewport}. Use WIDTHxHEIGHT (e.g. 1280x720)[/red]")
        raise typer.Exit(1)

    # Validate auth inputs
    if auth_state is not None and not auth_state.exists():
        err_console.print(f"[red]Auth state file not found: {auth_state}[/red]")
        raise typer.Exit(1)

    if login_url and not login_username:
        err_console.print("[red]--login-url requires --login-username[/red]")
        raise typer.Exit(1)

    if login_url and not login_password:
        err_console.print(
            "[red]--login-url requires --login-password (or set A11Y_LOGIN_PASSWORD env var)[/red]"
        )
        raise typer.Exit(1)

    report_formats = [f.strip() for f in formats.split(",")]
    valid_modes = {"automated", "keyboard", "semantic", "visual", "interaction",
                   "dynamic", "manual-assist", "ai-analysis", "full"}
    if mode not in valid_modes:
        err_console.print(f"[red]Invalid mode: {mode}. Choose from: {', '.join(sorted(valid_modes))}[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold blue]♿ AI Accessibility Testing Agent[/bold blue]")
    console.print(f"   URL: [link={url}]{url}[/link]")
    console.print(f"   Mode: {mode}")
    console.print(f"   Agentic: {agentic}")
    console.print(f"   Browser: {browser} ({'headless' if headless else 'headed'})")
    console.print(f"   Viewport: {viewport}")
    if auth_state:
        console.print(f"   Auth: state file ({auth_state.name})")
    elif login_url:
        console.print(f"   Auth: auto-login at {login_url}")
    console.print()

    # Run the scan
    from accessibility_agent.agent.orchestrator import ScanOrchestrator

    orchestrator = ScanOrchestrator(
        url=url,
        mode=mode,
        output_dir=output,
        browser_type=browser,
        headless=headless,
        viewport=(w, h),
        report_formats=report_formats,
        agentic=agentic,
        auth_state_path=auth_state,
        login_url=login_url,
        login_username=login_username,
        login_password=login_password,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(f"Scanning {url}...", total=None)
        try:
            result = asyncio.run(orchestrator.run())
        except KeyboardInterrupt:
            console.print("\n[yellow]Scan interrupted by user.[/yellow]")
            raise typer.Exit(130)
        except Exception as exc:
            err_console.print(f"\n[red]Scan failed: {exc}[/red]")
            log.exception("cli.scan_failed", error=str(exc))
            raise typer.Exit(1)

    # Display summary
    _print_summary(result)


def _print_summary(result: "ScanResult") -> None:  # type: ignore[name-defined]
    """Print a rich summary table of the scan results."""
    from accessibility_agent.wcag.schemas import ScanResult  # local import

    m = result.metrics
    console.print("\n[bold]📊 Scan Complete[/bold]\n")

    table = Table(title=f"Results — {result.run_id}", show_header=True)
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")

    table.add_row("[red]Confirmed Findings[/red]", str(m.confirmed_findings))
    table.add_row("[yellow]Likely Findings[/yellow]", str(m.likely_findings))
    table.add_row("[blue]Manual Review Required[/blue]", str(m.manual_review_findings))
    table.add_row("[green]Passed Checks[/green]", str(m.passed_checks))
    table.add_row("Duplicates Removed", str(m.duplicate_findings))
    table.add_row("Level A Failures", str(m.level_a_findings))
    table.add_row("Level AA Failures", str(m.level_aa_findings))
    table.add_row("axe Violations", str(m.axe_violations))
    table.add_row("axe Incomplete", str(m.axe_incomplete))

    console.print(table)

    if m.wcag_criteria_affected:
        console.print(f"\n[bold]WCAG Criteria Affected:[/bold] {', '.join(sorted(m.wcag_criteria_affected))}")

    if result.errors:
        console.print("\n[yellow]⚠️ Scan Errors:[/yellow]")
        for e in result.errors:
            console.print(f"  • {e}")

    console.print(
        "\n[dim]⚠️  A clean scan does NOT constitute WCAG conformance. "
        "Human expert review is required.[/dim]\n"
    )


@app.command("version")
def cmd_version() -> None:
    """Display version and dependency information."""
    from accessibility_agent import __version__
    from accessibility_agent.config import settings

    console.print(f"[bold]AI Accessibility Testing Agent[/bold] v{__version__}")
    console.print(f"  axe-core version: {settings.axe_version}")
    console.print(f"  Default browser: {settings.browser_type.value}")
    console.print(f"  LLM provider: {settings.llm_provider.value}")


@app.command("serve")
def cmd_serve(
    host: Annotated[str, typer.Option("--host", help="Host to bind the server to")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port to bind the server to")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Enable auto-reload for development")] = False,
) -> None:
    """
    Start the REST API server.

    Examples:
    
    \b
    # Start on default port 8000
    a11y-agent serve

    \b
    # Start on custom host/port
    a11y-agent serve --host 0.0.0.0 --port 8080
    """
    print(f"\nStarting API Server on http://{host}:{port}\n", flush=True)
    try:
        from accessibility_agent.api.server import run_server
        run_server(host=host, port=port, reload=reload)
    except ImportError as e:
        err_console.print(f"[red]Error starting server: {e}[/red]")
        err_console.print("Ensure you have installed fastapi and uvicorn: pip install fastapi uvicorn")
        raise typer.Exit(1)



@app.command("remediate")
def cmd_remediate(
    finding_file: Annotated[
        Optional[Path],
        typer.Option("--finding", "-f", help="Path to a JSON file containing the accessibility finding"),
    ] = None,
    finding_json: Annotated[
        Optional[str],
        typer.Option("--json", "-j", help="Raw JSON string of the finding (alternative to --finding)"),
    ] = None,
    repo: Annotated[
        Path,
        typer.Option("--repo", "-r", help="Path to the target application's source repository"),
    ] = Path("."),
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Plan and validate the fix without applying it to git"),
    ] = False,
    no_tests: Annotated[
        bool,
        typer.Option("--no-tests", help="Skip running the test suite after patching"),
    ] = False,
    no_pr: Annotated[
        bool,
        typer.Option("--no-pr", help="Skip opening a GitHub Pull Request"),
    ] = False,
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Write the full result JSON to this file"),
    ] = None,
) -> None:
    """
    Autonomously remediate a single accessibility finding.

    Accepts a finding (from a previous scan) and runs the full pipeline:
    locate -> classify -> plan -> patch -> validate -> git branch -> tests -> PR.

    Examples:

    \b
    # Remediate from a saved finding file
    a11y-agent remediate --finding ./reports/finding_A11Y-XXXX.json --repo ./my-app

    \b
    # Dry-run: plan and validate without touching git
    a11y-agent remediate --finding ./finding.json --repo . --dry-run

    \b
    # Pipe JSON inline (useful in CI scripts)
    a11y-agent remediate --json '{"finding_id": "A11Y-001", ...}' --repo .
    """
    import json

    log = get_logger("cli.remediate")

    # ── Load finding ──────────────────────────────────────────────────────────
    if finding_file is None and finding_json is None:
        err_console.print("[red]Error: provide either --finding <file> or --json <string>[/red]")
        raise typer.Exit(1)

    try:
        if finding_file is not None:
            raw = finding_file.read_text(encoding="utf-8")
        else:
            raw = finding_json  # type: ignore[assignment]
        finding_data = json.loads(raw)
        finding = Finding.model_validate(finding_data)
    except Exception as exc:
        err_console.print(f"[red]Failed to parse finding: {exc}[/red]")
        raise typer.Exit(1)

    repo_path = repo.resolve()
    if not repo_path.exists():
        err_console.print(f"[red]Repository path does not exist: {repo_path}[/red]")
        raise typer.Exit(1)

    # ── Header ────────────────────────────────────────────────────────────────
    console.print(f"\n[bold blue]♿ AI Accessibility Remediation Agent[/bold blue]")
    console.print(f"   Finding ID : [bold]{finding.finding_id}[/bold]")
    console.print(f"   Rule       : {finding.rule_id}")
    console.print(f"   WCAG SC    : {finding.wcag.success_criterion}")
    console.print(f"   URL        : [link={finding.url}]{finding.url}[/link]")
    console.print(f"   Repo       : {repo_path}")
    console.print(f"   Mode       : {'dry-run' if dry_run else 'live'}\n")

    # ── Run agent ─────────────────────────────────────────────────────────────
    agent = RemediationAgent(
        repo_path=repo_path,
        dry_run=dry_run,
        block_on_test_failure=not no_tests,
        create_pr=not no_pr,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(f"Remediating {finding.finding_id}…", total=None)
        try:
            result = agent.remediate(finding)
        except KeyboardInterrupt:
            console.print("\n[yellow]Remediation interrupted by user.[/yellow]")
            raise typer.Exit(130)
        except Exception as exc:
            err_console.print(f"\n[red]Remediation failed unexpectedly: {exc}[/red]")
            log.exception("cli.remediate_failed", error=str(exc))
            raise typer.Exit(1)

    # ── Print result ──────────────────────────────────────────────────────────
    _print_remediation_result(result, dry_run=dry_run)

    # ── Save result JSON ──────────────────────────────────────────────────────
    if output is not None:
        try:
            from dataclasses import asdict, fields
            import dataclasses

            def _serialize(obj):
                if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
                    return {f.name: _serialize(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
                if hasattr(obj, "model_dump"):
                    return obj.model_dump()
                if hasattr(obj, "value"):
                    return obj.value
                if isinstance(obj, list):
                    return [_serialize(i) for i in obj]
                return obj

            output.write_text(json.dumps(_serialize(result), indent=2, default=str), encoding="utf-8")
            console.print(f"\n[dim]Result saved to: {output}[/dim]")
        except Exception as exc:
            err_console.print(f"[yellow]Warning: could not save result JSON: {exc}[/yellow]")

    # ── Exit code ─────────────────────────────────────────────────────────────
    if not result.succeeded and result.status.value not in ("manual_review",):
        raise typer.Exit(1)


def _print_remediation_result(result: "RemediationAgentResult", dry_run: bool = False) -> None:  # type: ignore[name-defined]
    """Print a rich result panel after remediation."""
    from accessibility_agent.remediation.schemas import RemediationStatus

    status_color = {
        RemediationStatus.VERIFIED: "bold green",
        RemediationStatus.FAILED: "bold red",
        RemediationStatus.ROLLED_BACK: "bold yellow",
        RemediationStatus.MANUAL_REVIEW: "bold cyan",
        RemediationStatus.REJECTED: "red",
        RemediationStatus.IN_PROGRESS: "yellow",
    }.get(result.status, "white")

    status_icon = {
        RemediationStatus.VERIFIED: "✅",
        RemediationStatus.FAILED: "❌",
        RemediationStatus.ROLLED_BACK: "↩️",
        RemediationStatus.MANUAL_REVIEW: "🔍",
    }.get(result.status, "⏳")

    console.print(f"\n[bold]🔧 Remediation Result[/bold]\n")

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold dim", width=22)
    table.add_column("Value")

    table.add_row("Status", f"[{status_color}]{status_icon} {result.status.value.upper()}[/{status_color}]")
    table.add_row("Finding ID", result.finding_id)
    table.add_row("Attempts", str(result.attempts))
    table.add_row("Duration", f"{result.duration_seconds:.2f}s")

    if result.source_location:
        table.add_row("Source File", str(result.source_location.file_path))
        table.add_row("Source Line", str(result.source_location.start_line))

    if result.patches:
        p = result.patches[-1]
        table.add_row("Patch", f"+{p.lines_added} / -{p.lines_removed} lines in {p.target_file}")

    if not dry_run:
        table.add_row("Tests Ran", "Yes" if result.tests_ran else "No (not detected)")
        table.add_row("Tests Passed", "[green]Yes[/green]" if result.tests_passed else "[red]No[/red]")

    if result.verification_status:
        table.add_row("Verification", result.verification_status.value)

    if result.regressions_introduced:
        table.add_row("Regressions", f"[red]{result.regressions_introduced} new issue(s)[/red]")

    if result.pr_url:
        table.add_row("Pull Request", f"[link={result.pr_url}]{result.pr_url}[/link]")

    if result.failure_reason:
        table.add_row("Failure Reason", f"[red]{result.failure_reason}[/red]")

    if result.manual_review_notes:
        table.add_row("Manual Review", result.manual_review_notes[:100])

    console.print(table)
    console.print(
        "\n[dim]⚠️  Auto-generated fixes require human review before merging.[/dim]\n"
    )


if __name__ == "__main__":
    app()
