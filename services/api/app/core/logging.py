"""Structured logging.

JSON to stdout in every environment except a developer's terminal, where a
coloured console renderer is easier to read. Per CLAUDE.md every log line
carries its context (``request_id``, ``camera_id``, ``track_id``, ``event_id``)
rather than embedding those values in a formatted message string — that is
what makes the logs greppable when 80,000 cameras are talking at once.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from app.core.config import settings

# Correlation id for the in-flight request, bound by RequestContextMiddleware
# and picked up automatically by every log call made while handling it.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def _add_request_id(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Attach the current request id, when there is one."""
    request_id = request_id_var.get()
    if request_id is not None:
        event_dict["request_id"] = request_id
    return event_dict


def _add_service_context(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Tag every line with the emitting service and environment."""
    event_dict.setdefault("service", "api")
    event_dict.setdefault("environment", settings.environment)
    return event_dict


def configure_logging(*, service: str = "api", json_output: bool | None = None) -> None:
    """Configure structlog and route the stdlib logging tree through it.

    Args:
        service: Name recorded on every line. Each service passes its own.
        json_output: Force JSON on/off. Defaults to JSON everywhere except
            local development, where the console renderer is used.
    """
    if json_output is None:
        json_output = settings.environment != "development"

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_request_id,
        _add_service_context,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    # structlog hands the event dict to the stdlib handler rather than
    # rendering it itself: `wrap_for_formatter` must be the final processor.
    # Ending this chain with the renderer instead would render the line here
    # *and* again in the ProcessorFormatter below, emitting each structlog
    # event nested inside a second formatted line.
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, sqlalchemy, httpx) through the same
    # pipeline so the output is one consistent stream, not two formats.
    # `foreign_pre_chain` applies to records that did not originate in
    # structlog, giving third-party logs the same context keys as ours.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                renderer,
            ],
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # uvicorn installs its own handlers; strip them so lines are not doubled.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(noisy)
        lg.handlers.clear()
        lg.propagate = True

    # SQLAlchemy echoes every statement at INFO, which is unreadable at scale.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.db_echo else logging.WARNING
    )

    structlog.get_logger(service).info(
        "logging.configured",
        service=service,
        level=settings.log_level,
        format="json" if json_output else "console",
    )


def get_logger(name: str = "api") -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
