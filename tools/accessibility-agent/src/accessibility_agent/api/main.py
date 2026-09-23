import asyncio
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from accessibility_agent.api.routes import router
from accessibility_agent.config import settings

# ── Windows Event Loop Fix ────────────────────────────────────────────────────
# Python 3.8+ on Windows defaults to WindowsSelectorEventLoop inside uvicorn
# worker subprocesses (when --reload is used). Playwright requires
# WindowsProactorEventLoop to spawn browser subprocesses.
# This must be set at module import time, before any event loop is created.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="AI Accessibility Testing Agent API",
        description="REST API for triggering and managing WCAG accessibility scans.",
        version="0.1.0",
    )

    # Configure CORS for frontend clients
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API routes
    app.include_router(router)

    # Ensure reports directory exists for static mounting
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    
    # Mount the reports directory so HTML files can be viewed directly
    app.mount("/reports", StaticFiles(directory=str(settings.report_dir)), name="reports")

    @app.get("/", include_in_schema=False)
    async def root():
        """Redirect root to API documentation."""
        return RedirectResponse(url="/docs")

    return app

app = create_app()
