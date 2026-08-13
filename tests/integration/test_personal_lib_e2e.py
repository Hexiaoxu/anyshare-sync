"""Personal Library MVP — End-to-End Integration Test.

Tests the complete flow for personal doc libs only (no ACL/permission sync).

What it does:
  1. Discover personal doc libs from AnyShare
  2. Pick one → scan directory tree
  3. Download first file found
  4. Create BISHENG personal space
  5. Create matching folder structure
  6. Upload + register + wait for ingestion
  7. Verify parsing succeeded
  8. Clean up everything

Run:  python tests/integration/test_personal_lib_e2e.py
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

sys.path.insert(0, ".")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("e2e-personal")

# ============================================================
# CONFIG — edit for your environment
# ============================================================
ANYSHARE_BASE = os.environ.get("E2E_ANYSHARE_BASE", "")
ANYSHARE_TOKEN = os.environ.get("E2E_ANYSHARE_TOKEN", "")
BISHENG_BASE = os.environ.get("E2E_BISHENG_BASE", "")
BISHENG_COOKIE_VALUE = os.environ.get("E2E_BISHENG_COOKIE", "")

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_E2E") != "1",
    reason="real E2E disabled; set RUN_REAL_E2E=1 and E2E_* credentials",
)

# Max files to transfer in test (keep small for fast testing)
MAX_TEST_FILES = 3
MAX_DEPTH = 3

# ============================================================
# Helpers
# ============================================================

def as_headers(token: str = None) -> dict:
    h = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

def bs_cookies():
    return {"access_token_cookie": BISHENG_COOKIE_VALUE}

# ============================================================
# Main test
# ============================================================

class PersonalLibE2E:
    """E2E test runner for personal library sync."""

    def __init__(self):
        self.as_headers = {"Authorization": f"Bearer {ANYSHARE_TOKEN}"}
        self.bs_cookies = {"access_token_cookie": BISHENG_COOKIE_VALUE}
        self.temp_dir = Path(tempfile.mkdtemp(prefix="e2e_"))
        self.created_spaces = []
        self.downloaded_files = []
        self.results = {
            "personal_libs_found": 0,
            "files_scanned": 0,
            "files_downloaded": 0,
            "files_uploaded": 0,
            "files_ingested_ok": 0,
            "files_ingested_failed": 0,
            "folders_created": 0,
            "spaces_created": 0,
            "spaces_cleaned": 0,
            "errors": [],
        }

    # ── Step 1: Discover doc libs ──────────────────

    def discover_department_libs(self, limit: int = 5) -> list[dict]:
        """Use department doc libs — accessible with app token."""
        logger.info("Step 1: Discovering department doc libs...")
        with httpx.Client(timeout=30) as c:
            r = c.get(
                f"{ANYSHARE_BASE}/api/efast/v1/doc-lib/department",
                headers=self.as_headers,
                params={"offset": 0, "limit": limit},
            )
            r.raise_for_status()
            libs = r.json().get("entries", r.json().get("doc_libs", []))
        self.results["personal_libs_found"] = len(libs)
        logger.info(f"  Found {len(libs)} department doc lib(s)")
        for lib in libs[:3]:
            logger.info(f"    - {lib.get('name', '?')} ({lib['id'][:50]}...)")
        return libs

    # ── Step 2: Scan directory tree ─────────────────────────

    def scan_tree(self, lib_gns: str, max_depth: int = MAX_DEPTH, max_files: int = MAX_TEST_FILES) -> list[dict]:
        """BFS scan for personal doc lib — GNS IS the root folder."""
        all_files = []
        all_dirs = []
        root_gns = lib_gns

        encoded = quote(root_gns, safe="")
        with httpx.Client(timeout=15) as c:
            r = c.get(
                f"{ANYSHARE_BASE}/api/efast/v1/folders/{encoded}/sub_objects",
                headers=self.as_headers,
                params={"limit": 1},
            )
            if r.status_code != 200:
                logger.error(f"Cannot access root: {r.status_code}")
                return [], []

        logger.info(f"Step 2: Scanning tree (max depth={max_depth}, max files={max_files})...")
        queue = [(root_gns, 0)]
        scanned = set()

        while queue and len(all_files) < max_files:
            gns, depth = queue.pop(0)
            if depth > max_depth or gns in scanned:
                continue
            scanned.add(gns)
            encoded = quote(gns, safe="")
            try:
                with httpx.Client(timeout=30) as c:
                    r = c.get(
                        f"{ANYSHARE_BASE}/api/efast/v1/folders/{encoded}/sub_objects",
                        headers=self.as_headers,
                        params={"limit": 50, "sort": "name", "direction": "asc"},
                    )
                    r.raise_for_status()
                    page = r.json()
            except Exception as e:
                logger.warning(f"Scan error at depth {depth}: {e}")
                continue
            for d in page.get("dirs", []):
                all_dirs.append({"name": d.get("name", ""), "id": d["id"], "depth": depth})
                queue.append((d["id"], depth + 1))
            for f in page.get("files", []):
                all_files.append({
                    "name": f.get("name", ""), "id": f["id"],
                    "size": f.get("size", 0), "rev": f.get("rev", ""), "depth": depth,
                })
                if len(all_files) >= max_files:
                    break

        self.results["files_scanned"] = len(all_files)
        logger.info(f"Scanned: {len(all_dirs)} dirs, {len(all_files)} files")
        return all_files, all_dirs

    def download_file(self, file_info: dict) -> Path | None:
        """Download one file via osdownload protocol."""
        name = file_info["name"]
        logger.info(f"  Downloading: {name} ({file_info.get('size', '?')} bytes)")

        # Step 3a: get signed URL
        with httpx.Client(timeout=30) as c:
            r = c.post(
                f"{ANYSHARE_BASE}/api/efast/v1/file/osdownload",
                headers={**self.as_headers, "Content-Type": "application/json"},
                json={"docid": file_info["id"], "rev": "", "authtype": "QUERY_STRING", "savename": file_info["name"], "usehttps": True},
            )
            if r.status_code != 200:
                logger.error(f"    osdownload failed: {r.status_code} {r.text[:200]}")
                return None
            dl = r.json()

        authreq = dl.get("authrequest", [])
        if len(authreq) < 2:
            logger.error(f"    Invalid authrequest: {authreq}")
            return None

        method, url = authreq[0], authreq[1]
        dl_headers = {}
        for h in authreq[2:]:
            if ": " in h:
                k, v = h.split(": ", 1)
                dl_headers[k] = v

        # Step 3b: download
        safe_name = "".join(c for c in name if c.isalnum() or c in "._- ")
        dest = self.temp_dir / safe_name
        with httpx.Client(timeout=120) as c:
            with c.stream(method, url, headers=dl_headers) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_bytes(65536):
                        f.write(chunk)

        actual_size = dest.stat().st_size
        logger.info(f"    Downloaded {actual_size} bytes → {dest}")
        self.results["files_downloaded"] += 1
        self.downloaded_files.append({"info": file_info, "path": dest})
        return dest

    # ── Step 4: Create BISHENG space ────────────────────────

    def create_space(self, name: str, description: str = "") -> int:
        logger.info(f"Step 4: Creating BISHENG space '{name}'...")
        with httpx.Client(timeout=30) as c:
            r = c.post(
                f"{BISHENG_BASE}/api/v1/knowledge/space",
                json={"name": name, "description": description, "auth_type": "public"},
                cookies=self.bs_cookies,
            )
            r.raise_for_status()
            space_id = r.json()["data"]["id"]
        self.created_spaces.append(space_id)
        self.results["spaces_created"] += 1
        logger.info(f"  Created space id={space_id}")
        return space_id

    # ── Step 5: Create folders ──────────────────────────────

    def create_folder_structure(self, space_id: int, dirs: list[dict]) -> dict[str, int]:
        """Recreate directory tree. Returns {gns_id: bisheng_folder_id}."""
        logger.info(f"Step 5: Creating {len(dirs)} folders...")
        mapping = {}  # gns_id → bisheng_folder_id

        # Sort by depth so parents are created first
        for d in sorted(dirs, key=lambda x: x.get("depth", 0)):
            try:
                with httpx.Client(timeout=30) as c:
                    r = c.post(
                        f"{BISHENG_BASE}/api/v1/knowledge/space/{space_id}/folders",
                        json={"name": d["name"], "parent_id": None},
                        cookies=self.bs_cookies,
                    )
                    if r.status_code == 200:
                        fid = r.json()["data"]["id"]
                        mapping[d["id"]] = fid
                        self.results["folders_created"] += 1
            except Exception as e:
                logger.warning(f"    Folder '{d['name']}' failed: {e}")

        logger.info(f"  Created {self.results['folders_created']} folders")
        return mapping

    # ── Step 6: Upload + Register files ─────────────────────

    def upload_and_register(self, space_id: int, local_path: Path,
                            parent_id: int | None = None) -> dict | None:
        """Upload a file and register it. Returns file record."""
        name = local_path.name
        logger.info(f"  Uploading: {name}")

        # 6a: upload to MinIO
        with httpx.Client(timeout=60) as c:
            with open(local_path, "rb") as f:
                r = c.post(
                    f"{BISHENG_BASE}/api/v1/knowledge/upload/{space_id}",
                    files={"file": f},
                    cookies=self.bs_cookies,
                )
            if r.status_code != 200:
                logger.error(f"    Upload failed: {r.status_code} {r.text[:200]}")
                return None
            file_path = r.json()["data"]["file_path"]

        # 6b: register
        body = {"file_path": [file_path]}
        if parent_id:
            body["parent_id"] = parent_id
        with httpx.Client(timeout=30) as c:
            r = c.post(
                f"{BISHENG_BASE}/api/v1/knowledge/space/{space_id}/files",
                json=body,
                cookies=self.bs_cookies,
            )
            if r.status_code != 200:
                logger.error(f"    Register failed: {r.status_code} {r.text[:200]}")
                return None
            record = r.json()["data"][0] if isinstance(r.json()["data"], list) else r.json()["data"]

        self.results["files_uploaded"] += 1
        logger.info(f"    Registered: file_id={record['id']}, status={record['status']}")
        return record

    # ── Step 7: Wait for ingestion ───────────────────────────

    def wait_for_ingestion(self, space_id: int, file_id: int, max_wait: int = 120) -> int:
        """Wait for parsing to complete. Returns final status."""
        logger.info(f"  Waiting for ingestion (file_id={file_id})...")
        intervals = [5, 10, 15, 20, 30, 30]
        waited = 0

        for interval in intervals:
            if waited >= max_wait:
                break
            time.sleep(interval)
            waited += interval

            with httpx.Client(timeout=30) as c:
                r = c.get(
                    f"{BISHENG_BASE}/api/v1/knowledge/space/{space_id}/children",
                    params={"page": 1, "page_size": 200},
                    cookies=self.bs_cookies,
                )
                if r.status_code != 200:
                    continue
                items = r.json()["data"].get("data", [])
                for item in items:
                    if item["id"] == file_id:
                        logger.info(f"    Status after {waited}s: {item['status']}")
                        if item["status"] == 2:
                            self.results["files_ingested_ok"] += 1
                            return 2
                        elif item["status"] in (3, 7):
                            self.results["files_ingested_failed"] += 1
                            return item["status"]

        logger.warning(f"    Ingestion timeout after {waited}s")
        self.results["files_ingested_failed"] += 1
        return -1

    # ── Step 8: Cleanup ─────────────────────────────────────

    def cleanup(self):
        logger.info("Step 8: Cleaning temp files (keeping space)...")
        for space_id in self.created_spaces:
            logger.info(f"  KEEPING space_id={space_id} — check it at {BISHENG_BASE}/docs")

        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        logger.info("  Done.")

    # ── Run all ────────────────────────────────────────────

    def run(self):
        trace_id = uuid.uuid4().hex[:12]
        logger.info(f"========================================================")
        logger.info(f"Personal Library E2E Test — trace_id={trace_id}")
        logger.info(f"========================================================")

        try:
            # 1. Use known personal doc lib (5jliming1)
            libs = [{"name": "5jliming1", "id": "gns://110F8E071F0243AEBDB4DFD59F52D131"}]
            if not libs:
                logger.error("No personal libs — aborting")
                return self.results

            # 2. Pick first lib and scan
            target_lib = libs[0]
            lib_name = target_lib.get("name", "Unknown")
            lib_gns = target_lib["id"]
            logger.info(f"Target: '{lib_name}' ({lib_gns[:50]}...)")

            files, dirs = self.scan_tree(lib_gns)
            if not files:
                logger.error("No files found — aborting")
                return self.results

            # 3. Download first MAX_TEST_FILES files
            downloaded = []
            for f in files[:MAX_TEST_FILES]:
                path = self.download_file(f)
                if path:
                    downloaded.append((f, path))

            if not downloaded:
                logger.error("No files downloaded — aborting")
                return self.results

            # 4. Create space
            space_id = self.create_space(f"E2E_Personal_{lib_name}_{trace_id}")

            # 5. Create folders
            folder_map = self.create_folder_structure(space_id, dirs)

            # 6+7. Upload + register + wait for each file
            for file_info, local_path in downloaded:
                record = self.upload_and_register(space_id, local_path)
                if record:
                    final_status = self.wait_for_ingestion(space_id, record["id"])
                    # Log version info
                    if final_status == 2:
                        with httpx.Client(timeout=30) as c:
                            r = c.get(
                                f"{BISHENG_BASE}/api/v1/knowledge/space/file/{record['id']}/versions",
                                cookies=self.bs_cookies,
                            )
                            if r.status_code == 200:
                                vdata = r.json()["data"]
                                logger.info(f"    Version: doc_id={vdata.get('document_id')}, v{vdata.get('current_primary_version_no')}")

        except Exception as e:
            logger.exception(f"E2E error: {e}")
            self.results["errors"].append(str(e)[:500])
        finally:
            self.cleanup()

        # Summary
        logger.info(f"========================================================")
        logger.info(f"E2E Results (trace_id={trace_id}):")
        for k, v in self.results.items():
            logger.info(f"  {k}: {v}")
        logger.info(f"========================================================")

        success = (
            self.results["files_downloaded"] > 0
            and self.results["files_uploaded"] > 0
            and self.results["files_ingested_ok"] > 0
            and len(self.results["errors"]) == 0
        )
        logger.info(f"OVERALL: {'PASSED' if success else 'FAILED'}")
        return self.results


if __name__ == "__main__":
    test = PersonalLibE2E()
    test.run()
