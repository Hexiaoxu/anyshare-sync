"""Tracks BISHENG file ingestion (parsing) status until SUCCESS or failure."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import IntEnum

logger = logging.getLogger(__name__)


class FileStatus(IntEnum):
    PROCESSING = 1
    SUCCESS = 2
    FAILED = 3
    REBUILDING = 4
    WAITING = 5
    TIMEOUT = 6
    VIOLATION = 7


@dataclass
class IngestionResult:
    status: int
    waited_seconds: int


class IngestionTracker:
    """Polls BISHENG for file ingestion status with progressive backoff."""

    _POLL_INTERVALS = [30, 60, 120, 300, 600]   # seconds
    _FALLBACK = 1800                              # 30 min
    _MAX_WAIT = 86400                             # 24 hours

    def __init__(self, get_status_func):
        """*get_status_func*: callable(file_id) -> int (status)."""
        self._get_status = get_status_func

    def wait(self, file_id: int) -> IngestionResult:
        """Poll until terminal status or timeout."""
        waited = 0

        # Active polling phase
        for interval in self._POLL_INTERVALS:
            time.sleep(interval)
            waited += interval
            status = self._get_status(file_id)
            logger.debug(f"File {file_id}: status={status} after {waited}s")
            if status in (
                FileStatus.SUCCESS,
                FileStatus.FAILED,
                FileStatus.VIOLATION,
            ):
                return IngestionResult(status=status, waited_seconds=waited)

        # Passive polling phase
        while waited < self._MAX_WAIT:
            time.sleep(self._FALLBACK)
            waited += self._FALLBACK
            status = self._get_status(file_id)
            if status in (
                FileStatus.SUCCESS,
                FileStatus.FAILED,
                FileStatus.VIOLATION,
            ):
                return IngestionResult(status=status, waited_seconds=waited)

        return IngestionResult(status=FileStatus.TIMEOUT, waited_seconds=waited)
