"""AnyShare file download via osdownload → signed URL."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


@dataclass
class DownloadInfo:
    """Result of osdownload — auth request + metadata."""
    http_method: str
    url: str
    headers: dict[str, str]
    name: str
    size: int
    rev: str


class AnyShareDownloader:
    """Downloads files from AnyShare via the three-step protocol."""

    CHUNK_SIZE = 64 * 1024  # 64KB

    def __init__(self, base_url: str, get_user_token, timeout: float = 120.0):
        self._url = base_url.rstrip("/")
        self._get_token = get_user_token
        self._timeout = timeout

    def get_download_info(self, doc_id: str, name: str = "") -> DownloadInfo:
        """Step 1: call osdownload to get signed URL."""
        with httpx.Client(timeout=httpx.Timeout(30)) as client:
            resp = client.post(
                f"{self._url}/api/efast/v1/file/osdownload",
                json={
                    "docid": doc_id,
                    "rev": "",
                    "authtype": "QUERY_STRING",
                    "savename": name or "",
                    "usehttps": True,
                },
                headers={"Authorization": f"Bearer {self._get_token()}"},
            )
            resp.raise_for_status()
            data = resp.json()

        authreq = data["authrequest"]
        return DownloadInfo(
            http_method=authreq[0],
            url=authreq[1],
            headers=self._parse_auth_headers(authreq[2:]),
            name=data.get("name", ""),
            size=data.get("size", 0),
            rev=data.get("rev", ""),
        )

    def download_to_file(self, info: DownloadInfo, dest_dir: Path) -> Path:
        """Step 2: download via signed URL to local file. Returns path."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / info.name

        with httpx.Client(timeout=httpx.Timeout(self._timeout)) as client:
            with client.stream(info.http_method, info.url, headers=info.headers) as resp:
                resp.raise_for_status()
                actual_size = 0
                with open(dest_path, "wb") as f:
                    for chunk in resp.iter_bytes(self.CHUNK_SIZE):
                        f.write(chunk)
                        actual_size += len(chunk)

        # Verify size
        if info.size > 0 and actual_size != info.size:
            logger.warning(
                f"Size mismatch for {info.name}: "
                f"expected {info.size}, got {actual_size}"
            )

        logger.info(f"Downloaded {info.name} ({actual_size} bytes)")
        return dest_path

    def checksum(self, file_path: Path) -> str:
        """Compute SHA-256 hex digest."""
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(self.CHUNK_SIZE)
                if not chunk:
                    break
                sha.update(chunk)
        return sha.hexdigest()

    @staticmethod
    def _parse_auth_headers(raw: list[str]) -> dict[str, str]:
        headers = {}
        for item in raw:
            if ": " in item:
                key, val = item.split(": ", 1)
                headers[key] = val
        return headers
