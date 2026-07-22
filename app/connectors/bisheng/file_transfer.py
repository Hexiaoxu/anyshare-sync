"""BISHENG file upload, register, and ingestion status tracking."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from .client import BishengClient

logger = logging.getLogger(__name__)

# Polling backoff: 30s, 1m, 2m, 5m, 10m, then every 30m
_POLL_INTERVALS = [30, 60, 120, 300, 600]
_FALLBACK_INTERVAL = 1800
_MAX_TOTAL_WAIT = 86400  # 24 hours


class BishengFileTransfer:
    """Upload files to BISHENG and track parsing status."""

    def __init__(self, client: BishengClient):
        self._c = client

    def upload_to_minio(self, space_id: int, local_path: Path) -> str:
        """Upload file to MinIO temp bucket. Returns file_path (URL)."""
        resp = self._c._upload(
            f"/api/v1/knowledge/upload/{space_id}",
            str(local_path),
        )
        data = self._c.ok(resp)
        return data["data"]["file_path"]

    def register(self, space_id: int, file_path: str,
                 parent_id: int | None = None) -> dict:
        """Register an uploaded file to the knowledge space. Returns KnowledgeFile record."""
        body = {"file_path": [file_path]}
        if parent_id is not None:
            body["parent_id"] = parent_id
        resp = self._c._post(f"/api/v1/knowledge/space/{space_id}/files", body)
        data = self._c.ok(resp)
        return data["data"][0] if isinstance(data["data"], list) else data["data"]

    def get_status(self, file_id: int, space_id: int) -> int:
        """Get the parsing status of a file. Returns KnowledgeFileStatus value."""
        resp = self._c._get(
            f"/api/v1/knowledge/space/{space_id}/children",
            {"page": 1, "page_size": 200},
        )
        data = self._c.ok(resp)
        inner = data.get("data", {})
        items = inner.get("data", inner if isinstance(inner, list) else [])
        if not isinstance(items, list):
            items = []
        for item in items:
            if item["id"] == file_id:
                return item["status"]
        return -1  # not found

    def wait_until_done(self, file_id: int, space_id: int) -> int:
        """Block until parsing succeeds, fails, or times out. Returns final status."""
        waited = 0
        for i, interval in enumerate(_POLL_INTERVALS):
            time.sleep(interval)
            waited += interval
            status = self.get_status(file_id, space_id)
            logger.debug(f"File {file_id} status={status} after {waited}s")
            if status in (2, 3, 7):   # SUCCESS, FAILED, VIOLATION
                return status
            # Continue polling
        # After initial backoff, poll every 30 min
        while waited < _MAX_TOTAL_WAIT:
            time.sleep(_FALLBACK_INTERVAL)
            waited += _FALLBACK_INTERVAL
            status = self.get_status(file_id, space_id)
            if status in (2, 3, 7):
                return status
        return 6  # TIMEOUT

    def get_versions(self, file_id: int) -> dict:
        """Get document version list."""
        resp = self._c._get(f"/api/v1/knowledge/space/file/{file_id}/versions")
        return self._c.ok(resp)["data"]

    def delete_file(self, space_id: int, file_id: int) -> None:
        self._c._delete(f"/api/v1/knowledge/space/{space_id}/files/{file_id}")
