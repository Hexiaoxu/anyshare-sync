"""AnyShare directory tree scanner (BFS + marker pagination).

Walks the entire folder tree under a document library, yielding
files and folders with their metadata.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


@dataclass
class AnyShareFile:
    id: str       # GNS
    name: str
    rev: str      # source revision
    size: int = 0
    parent_gns: str = ""


@dataclass
class AnyShareFolder:
    id: str       # GNS
    name: str
    rev: str
    parent_gns: str = ""


@dataclass
class ScanResult:
    folders: list[AnyShareFolder] = field(default_factory=list)
    files: list[AnyShareFile] = field(default_factory=list)
    total_folders: int = 0
    total_files: int = 0


class AnyShareScanner:
    """BFS directory tree walker for a single document library."""

    MAX_DEPTH = 20
    MAX_OBJECTS = 500_000

    # File extensions to skip (archives — can't be parsed by BISHENG)
    SKIP_EXTENSIONS = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".iso"}

    def __init__(self, base_url: str, get_token, timeout: float = 60.0):
        self._url = base_url.rstrip("/")
        self._get_token = get_token
        self._timeout = timeout
        self._skipped_count = 0

    def scan(self, doclib_gns: str) -> ScanResult:
        """Walk the entire tree under *doclib_gns*."""
        result = ScanResult()
        queue: deque[tuple[str, str]] = deque()
        queue.append((doclib_gns, ""))  # (gns_path, parent_gns)

        object_count = 0

        while queue:
            gns_path, parent_gns = queue.popleft()

            page = self._fetch_page(gns_path, marker=None)
            while page is not None:
                # Folders
                for d in page.get("dirs", []):
                    if result.total_folders >= self.MAX_OBJECTS:
                        logger.warning("Max objects reached, stopping scan")
                        return result
                    folder = AnyShareFolder(
                        id=d["id"], name=d.get("name", ""),
                        rev=d.get("rev", ""), parent_gns=parent_gns,
                    )
                    result.folders.append(folder)
                    result.total_folders += 1
                    object_count += 1
                    # Enqueue for deeper traversal
                    queue.append((d["id"], d["id"]))

                # Files (skip archive/compressed formats)
                for f in page.get("files", []):
                    name = f.get("name", "")
                    if self._is_archive(name):
                        self._skipped_count += 1
                        continue
                    if result.total_files >= self.MAX_OBJECTS:
                        return result
                    af = AnyShareFile(
                        id=f["id"], name=f.get("name", ""),
                        rev=f.get("rev", ""), size=f.get("size", 0),
                        parent_gns=parent_gns,
                    )
                    result.files.append(af)
                    result.total_files += 1
                    object_count += 1

                # Next page?
                marker = page.get("next_marker", "")
                if marker:
                    page = self._fetch_page(gns_path, marker=marker)
                else:
                    page = None

            logger.debug(
                f"Scanned {gns_path}: "
                f"{result.total_folders} folders, {result.total_files} files"
            )

        logger.info(
            f"Scan complete: {result.total_folders} folders, "
            f"{result.total_files} files"
            + (f", skipped {self._skipped_count} archives" if self._skipped_count else "")
        )
        return result

    def _is_archive(self, filename: str) -> bool:
        """Check if file should be skipped (archive/compressed format)."""
        return filename.lower().endswith(tuple(self.SKIP_EXTENSIONS))

    def _fetch_page(self, gns_path: str, marker: str | None = None) -> dict | None:
        """Fetch one page of sub_objects."""
        encoded = quote(gns_path, safe="")
        url = f"{self._url}/api/efast/v1/folders/{encoded}/sub_objects"
        params = {"limit": 100, "sort": "name", "direction": "asc"}
        if marker:
            params["marker"] = marker

        with httpx.Client(timeout=httpx.Timeout(self._timeout)) as client:
            resp = client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self._get_token()}"},
            )
            resp.raise_for_status()
            return resp.json()
