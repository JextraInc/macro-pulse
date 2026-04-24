import logging as stdlog
import sys
from typing import Any

import structlog


def configure_logging(level: str = "INFO") -> None:
    stdlog.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(stdlog, level.upper(), stdlog.INFO),
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.EventRenamer("event"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(stdlog, level.upper(), stdlog.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
