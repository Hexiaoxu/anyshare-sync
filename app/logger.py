"""Centralized logging with file rotation, trace IDs, and dual output."""

import logging
import sys
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Trace ID ──────────────────────────────────────────────────

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")


def set_trace_id(tid: str | None = None) -> str:
    """Set or generate a trace ID for the current sync run."""
    tid = tid or uuid.uuid4().hex[:12]
    _trace_id.set(tid)
    return tid


def get_trace_id() -> str:
    return _trace_id.get() or "-"


# ── Trace-aware formatter ────────────────────────────────────

class TraceFormatter(logging.Formatter):
    def format(self, record):
        tid = get_trace_id()
        record.trace_id = tid
        return super().format(record)


# ── Setup ─────────────────────────────────────────────────────

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# File format: timestamp, level, trace_id, logger, message
FILE_FORMAT = (
    "[{asctime}] [{levelname:7s}] [{trace_id}] "
    "{name}:{lineno} - {message}"
)

# Console format: shorter, no trace_id noise on info
CONSOLE_FORMAT = "[{asctime}] [{levelname:7s}] {message}"

_initialized = False


def setup_logging(level: str = "INFO"):
    """Initialize logging once. Safe to call multiple times."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # let handlers filter

    # Console handler (INFO+)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(logging.Formatter(
        CONSOLE_FORMAT, style="{", datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(console)

    # File handler (DEBUG+, rotated by size, 5 backups of 10MB each)
    file_handler = RotatingFileHandler(
        LOG_DIR / "sync.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(TraceFormatter(
        FILE_FORMAT, style="{", datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(file_handler)

    # Error file handler (ERROR+ only)
    error_handler = RotatingFileHandler(
        LOG_DIR / "error.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(TraceFormatter(
        FILE_FORMAT, style="{", datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(error_handler)

    # Quiet noisy libs
    for noisy in ["httpx", "httpcore", "urllib3", "sqlalchemy.engine"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a module."""
    return logging.getLogger(name)
