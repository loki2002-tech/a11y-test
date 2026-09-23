import asyncio
import sys
import uvicorn
import structlog

log = structlog.get_logger()

# ── Windows Event Loop Fix ────────────────────────────────────────────────────
# On Windows, Python 3.8+ defaults to WindowsSelectorEventLoop which cannot
# spawn subprocesses (needed by Playwright to launch browsers).
# We must switch to WindowsProactorEventLoop BEFORE uvicorn starts.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

def run_server(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Start the FastAPI server via uvicorn."""
    log.info("server.starting", host=host, port=port)
    uvicorn.run(
        "accessibility_agent.api.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
        loop="asyncio",
    )

if __name__ == "__main__":
    run_server()
