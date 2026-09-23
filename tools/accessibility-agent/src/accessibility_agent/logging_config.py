"""
Structured logging setup using structlog.

All components in the agent must import and use the logger from this module
to ensure consistent structured output and security (no secrets in logs).

Usage:
    from accessibility_agent.logging_config import get_logger

    log = get_logger(__name__)
    log.info("scan_started", url=url, browser=browser_type)
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from accessibility_agent.config import LogLevel, settings


def _get_stdlib_level(level: LogLevel) -> int:
    return getattr(logging, level.value, logging.INFO)


def configure_logging() -> None:
    """
    Configure structlog with shared processors.  Call once at startup
    (e.g. in cli.py or the test conftest).
    """
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
    ]

    if settings.log_json:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        # Only enable colors when we have a real terminal (TTY).
        # When running as a subprocess (uvicorn worker, CI/CD, tests),
        # there is no TTY and colorama crashes writing to a non-console stream.
        _use_colors = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
        renderer = structlog.dev.ConsoleRenderer(colors=_use_colors)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(_get_stdlib_level(settings.log_level))

    # Silence noisy libraries
    for lib in ("playwright", "asyncio", "httpx", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger for *name*."""
    return structlog.get_logger(name)  # type: ignore[return-value]
