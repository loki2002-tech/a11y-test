import asyncio
import uuid
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import structlog
from pydantic import BaseModel

from accessibility_agent.agent.orchestrator import ScanOrchestrator
from accessibility_agent.wcag.schemas import ScanResult

log = structlog.get_logger()

class JobStatus(BaseModel):
    run_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    progress: str = ""
    error: Optional[str] = None
    result: Optional[ScanResult] = None
    report_paths: Optional[Dict[str, str]] = None

class JobManager:
    """Manages background accessibility scans."""
    
    def __init__(self):
        self._jobs: Dict[str, JobStatus] = {}

    def get_job(self, run_id: str) -> Optional[JobStatus]:
        return self._jobs.get(run_id)

    async def run_job(
        self,
        url: str,
        mode: str = "automated",
        agentic: bool = False,
        browser_name: str = "chromium",
        headless: bool = True,
        viewport_str: str = "1280x720",
        # ── Authentication ────────────────────────────────────────────────
        auth_state_path: Optional[str] = None,
        login_url: Optional[str] = None,
        login_username: Optional[str] = None,
        login_password: Optional[str] = None,
    ) -> str:
        """Start a scan job in the background and return a unique run_id."""
        
        run_id = f"RUN-{uuid.uuid4().hex[:12].upper()}"
        now = datetime.now(timezone.utc)
        
        self._jobs[run_id] = JobStatus(
            run_id=run_id,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        
        # Fire and forget
        asyncio.create_task(self._process_job(
            run_id, url, mode, agentic, browser_name, headless, viewport_str,
            auth_state_path, login_url, login_username, login_password,
        ))
        
        return run_id

    async def _process_job(
        self,
        run_id: str,
        url: str,
        mode: str,
        agentic: bool,
        browser_name: str,
        headless: bool,
        viewport_str: str,
        auth_state_path: Optional[str] = None,
        login_url: Optional[str] = None,
        login_username: Optional[str] = None,
        login_password: Optional[str] = None,
    ) -> None:
        """Execute the scan orchestrated by ScanOrchestrator."""
        job = self._jobs[run_id]
        job.status = "running"
        job.progress = "Starting browser..."
        job.updated_at = datetime.now(timezone.utc)
        
        log.info("job.started", run_id=run_id, url=url)
        
        try:
            width, height = map(int, viewport_str.split("x"))
            
            orchestrator = ScanOrchestrator(
                url=url,
                mode=mode,
                agentic=agentic,
                browser_type=browser_name,
                headless=headless,
                viewport=(width, height),
                run_id=run_id,
                auth_state_path=Path(auth_state_path) if auth_state_path else None,
                login_url=login_url,
                login_username=login_username,
                login_password=login_password,
            )
            
            result = await orchestrator.run()
            
            from accessibility_agent.config import settings
            report_paths = {
                "json": str(settings.report_dir / f"{run_id}_report.json"),
                "html": str(settings.report_dir / f"{run_id}_report.html"),
            }
            
            job.result = result
            job.report_paths = report_paths
            job.status = "completed"
            job.progress = "Done"
            
            log.info("job.completed", run_id=run_id, findings=len(result.findings))
            
        except Exception as e:
            log.error("job.failed", run_id=run_id, error=str(e), exc_info=True)
            job.status = "failed"
            job.error = str(e)
            job.progress = "Failed"
        finally:
            job.updated_at = datetime.now(timezone.utc)

# Global singleton
job_manager = JobManager()
