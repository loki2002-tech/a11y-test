from typing import Dict, Any, Optional
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, BackgroundTasks, status
from pydantic import BaseModel, HttpUrl, Field

from accessibility_agent.agent.job_manager import job_manager

router = APIRouter(prefix="/api/v1", tags=["scans"])

class ScanRequest(BaseModel):
    url: HttpUrl = Field(..., description="The full URL of the website you want to scan (e.g., https://example.com)")
    mode: str = Field("automated", description="The type of scan. Supported: 'automated', 'full'")
    agentic: bool = Field(False, description="Set to true to enable the AI to click around and test dynamic elements.")
    browser: str = Field("chromium", description="The browser engine to use. Supported: 'chromium' (Chrome/Edge), 'firefox', or 'webkit' (Safari).")
    headless: bool = Field(True, description="Set to true to run the browser invisibly in the background. Set to false if you want to watch the browser pop up and run.")
    viewport: str = Field("1280x720", description="The screen size to simulate. Format: WIDTHxHEIGHT (e.g., '1920x1080' for desktop, '375x812' for mobile).")
    # ── Authentication (optional) ─────────────────────────────────────────────
    auth_state_path: Optional[str] = Field(
        None,
        description="Server-side path to a Playwright storage state JSON file. Use for OAuth/SSO-protected apps.",
    )
    login_url: Optional[str] = Field(
        None,
        description="URL of the login page for auto-fill authentication.",
    )
    login_username: Optional[str] = Field(
        None,
        description="Username or email for auto-login.",
    )
    login_password: Optional[str] = Field(
        None,
        description="Password for auto-login. Consider using server env vars instead of sending in API body.",
    )


class ScanResponse(BaseModel):
    run_id: str
    message: str

class StatusResponse(BaseModel):
    run_id: str
    status: str
    progress: str
    error: Optional[str] = None
    report_paths: Optional[Dict[str, str]] = None

@router.post("/scan", response_model=ScanResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_scan(request: ScanRequest) -> ScanResponse:
    """Start a new accessibility scan in the background."""
    run_id = await job_manager.run_job(
        url=str(request.url),
        mode=request.mode,
        agentic=request.agentic,
        browser_name=request.browser,
        headless=request.headless,
        viewport_str=request.viewport,
    )
    
    return ScanResponse(
        run_id=run_id,
        message="Scan started in the background. Check status using /api/v1/scans/{run_id}/status"
    )

@router.get("/scans/{run_id}/status", response_model=StatusResponse)
async def get_scan_status(run_id: str) -> StatusResponse:
    """Get the current status of a scan job."""
    job = job_manager.get_job(run_id)
    if not job:
        raise HTTPException(status_code=404, detail="Scan run_id not found")
        
    return StatusResponse(
        run_id=job.run_id,
        status=job.status,
        progress=job.progress,
        error=job.error,
        report_paths=job.report_paths
    )

@router.get("/scans/{run_id}/report")
async def get_scan_report(run_id: str) -> Any:
    """Get the final JSON report of a completed scan."""
    job = job_manager.get_job(run_id)
    if not job:
        raise HTTPException(status_code=404, detail="Scan run_id not found")
        
    if job.status != "completed":
        raise HTTPException(status_code=400, detail=f"Scan is currently {job.status}. Wait for completion.")
        
    if not job.result:
        raise HTTPException(status_code=500, detail="Scan completed but result is missing.")
        
    return job.result.model_dump(mode="json")


# ─────────────────────────────────────────────────────────────────────────────
# Remediation endpoints
# ─────────────────────────────────────────────────────────────────────────────

# In-memory store for remediation jobs (replace with Redis/DB in production)
_remediation_jobs: Dict[str, Dict[str, Any]] = {}
_thread_pool = ThreadPoolExecutor(max_workers=4)


class RemediationRequest(BaseModel):
    """Body for POST /remediate."""
    finding: Dict[str, Any] = Field(
        ...,
        description="The accessibility finding JSON (as returned by a scan). Must conform to the Finding schema.",
    )
    repo_path: str = Field(
        ...,
        description="Absolute or relative path to the target application's source repository on the server.",
    )
    dry_run: bool = Field(
        False,
        description="If true, plan and validate the fix but do not apply to git.",
    )
    no_tests: bool = Field(
        False,
        description="If true, skip running the test suite after patching.",
    )
    no_pr: bool = Field(
        False,
        description="If true, skip creating a GitHub Pull Request.",
    )


class RemediationJobResponse(BaseModel):
    """Immediate response for POST /remediate (async job started)."""
    job_id: str
    finding_id: str
    status: str
    message: str
    poll_url: str


class RemediationResultResponse(BaseModel):
    """Full result returned by GET /remediations/{job_id}."""
    job_id: str
    finding_id: str
    status: str
    attempts: int
    pr_url: str
    tests_ran: bool
    tests_passed: bool
    verification_status: Optional[str] = None
    regressions_introduced: int
    failure_reason: str
    manual_review_notes: str
    duration_seconds: float
    started_at: str
    completed_at: Optional[str] = None


def _run_remediation_sync(job_id: str, request: RemediationRequest) -> None:
    """Execute the RemediationAgent synchronously inside a thread pool worker."""
    from accessibility_agent.remediation.agent import RemediationAgent
    from accessibility_agent.wcag.schemas import Finding

    job = _remediation_jobs[job_id]
    job["status"] = "running"

    try:
        finding = Finding.model_validate(request.finding)
        repo_path = Path(request.repo_path).resolve()

        agent = RemediationAgent(
            repo_path=repo_path,
            dry_run=request.dry_run,
            block_on_test_failure=not request.no_tests,
            create_pr=not request.no_pr,
        )
        result = agent.remediate(finding)

        job["status"] = result.status.value
        job["result"] = {
            "finding_id": result.finding_id,
            "status": result.status.value,
            "attempts": result.attempts,
            "pr_url": result.pr_url,
            "tests_ran": result.tests_ran,
            "tests_passed": result.tests_passed,
            "verification_status": result.verification_status.value if result.verification_status else None,
            "regressions_introduced": result.regressions_introduced,
            "failure_reason": result.failure_reason,
            "manual_review_notes": result.manual_review_notes,
            "duration_seconds": result.duration_seconds,
        }

    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)

    finally:
        job["completed_at"] = datetime.now(timezone.utc).isoformat()


@router.post(
    "/remediate",
    response_model=RemediationJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["remediation"],
    summary="Autonomously remediate an accessibility finding",
)
async def start_remediation(request: RemediationRequest) -> RemediationJobResponse:
    """
    Submit a remediation job.

    The agent will:
    1. Locate the source file responsible for the finding.
    2. Classify whether it's safe to auto-remediate.
    3. Generate and validate a patch (up to 3 attempts).
    4. Apply the patch in a git branch and push.
    5. Run the project test suite.
    6. Open a GitHub Pull Request if configured.

    Returns a job_id for polling status via GET /api/v1/remediations/{job_id}.
    """
    # Validate the repo path before starting the job
    repo_path = Path(request.repo_path)
    if not repo_path.exists():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"repo_path does not exist on server: {request.repo_path}",
        )

    # Validate finding structure
    try:
        from accessibility_agent.wcag.schemas import Finding
        finding = Finding.model_validate(request.finding)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid finding JSON: {exc}",
        )

    job_id = str(uuid.uuid4())
    _remediation_jobs[job_id] = {
        "job_id": job_id,
        "finding_id": finding.finding_id,
        "status": "queued",
        "error": None,
        "result": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
    }

    # Run in thread pool so the async event loop is not blocked
    loop = asyncio.get_event_loop()
    loop.run_in_executor(_thread_pool, _run_remediation_sync, job_id, request)

    return RemediationJobResponse(
        job_id=job_id,
        finding_id=finding.finding_id,
        status="queued",
        message="Remediation job accepted. Poll /api/v1/remediations/{job_id} for status.",
        poll_url=f"/api/v1/remediations/{job_id}",
    )


@router.get(
    "/remediations/{job_id}",
    tags=["remediation"],
    summary="Poll the status of a remediation job",
)
async def get_remediation_status(job_id: str) -> Dict[str, Any]:
    """Get the current status and result of a remediation job."""
    job = _remediation_jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Remediation job '{job_id}' not found.",
        )
    return job


@router.get(
    "/remediations",
    tags=["remediation"],
    summary="List all remediation jobs",
)
async def list_remediations() -> Dict[str, Any]:
    """Return a summary of all remediation jobs in the current session."""
    return {
        "total": len(_remediation_jobs),
        "jobs": [
            {
                "job_id": j["job_id"],
                "finding_id": j["finding_id"],
                "status": j["status"],
                "started_at": j["started_at"],
                "completed_at": j.get("completed_at"),
            }
            for j in _remediation_jobs.values()
        ],
    }
