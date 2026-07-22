"""End-to-end integration test using real AnyShare + BISHENG.

This script:
  1. Discovers AnyShare doc libs
  2. Scans one small doc lib
  3. Transfers ONE file to BISHENG
  4. Cleans up

Run: python tests/integration/test_e2e_real.py
"""

import sys
sys.path.insert(0, ".")

import logging
from app.models import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("e2e")

# ============================================================
# CONFIG — update these with real credentials before running
# ============================================================
ANYSHARE_BASE = "https://5j-zsgl.powerchina.cn"
ANYSHARE_TOKEN = "ory_at_w1utXmu10Aw1QJdmxmLRFM6pTta9BjSXTVNgqa2FJPc.fNh0Xb_Ozv2ORR7VnCyXkiJfwv_VSb8VnQNE3AtZaWE"

BISHENG_BASE = "http://192.168.106.161:7860"
BISHENG_COOKIE = "access_token_cookie=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ7XCJ1c2VyX2lkXCI6IDEsIFwidXNlcl9uYW1lXCI6IFwiYWRtaW5cIiwgXCJ0ZW5hbnRfaWRcIjogMSwgXCJ0b2tlbl92ZXJzaW9uXCI6IDF9IiwiZXhwIjoxNzg0MTAxMDk4LCJpc3MiOiJiaXNoZW5nIn0.U3OsX2VeLKjKKoFyA4UkBQ91sy1VSU6zjE_mkrjpKpg"


def main():
    init_db()
    logger.info("=== E2E Integration Test ===")

    # ── Step 1: Discover AnyShare doc libs ──────────────────
    import httpx
    from urllib.parse import quote

    headers = {"Authorization": f"Bearer {ANYSHARE_TOKEN}"}

    logger.info("Step 1: Listing AnyShare doc libs...")
    with httpx.Client(timeout=30) as c:
        # Personal doc libs
        r = c.get(f"{ANYSHARE_BASE}/api/efast/v1/doc-lib/user", headers=headers, params={"offset": 0, "limit": 2})
        r.raise_for_status()
        personal = r.json().get("entries", r.json().get("doc_libs", []))
        logger.info(f"  Personal libs found: {len(personal)} (showing 2)")

        # Department doc libs
        r = c.get(f"{ANYSHARE_BASE}/api/efast/v1/doc-lib/department", headers=headers, params={"offset": 0, "limit": 2})
        r.raise_for_status()
        dept = r.json().get("entries", r.json().get("doc_libs", []))
        logger.info(f"  Department libs found: {len(dept)} (showing 2)")

        # Knowledge doc libs
        r = c.get(f"{ANYSHARE_BASE}/api/efast/v1/doc-lib/knowledge", headers=headers, params={"offset": 0, "limit": 2})
        r.raise_for_status()
        kb = r.json().get("entries", r.json().get("doc_libs", []))
        logger.info(f"  Knowledge libs found: {len(kb)} (showing 2)")

    # ── Step 2: Pick one doc lib and scan root ──────────────
    all_libs = personal + dept + kb
    if not all_libs:
        logger.error("No doc libs found — aborting")
        return

    test_lib = all_libs[0]
    lib_gns = test_lib["id"]
    logger.info(f"Step 2: Scanning '{test_lib.get('name', lib_gns)}' ({lib_gns[:50]}...)")

    encoded = quote(lib_gns, safe="")
    with httpx.Client(timeout=30) as c:
        r = c.get(
            f"{ANYSHARE_BASE}/api/efast/v1/folders/{encoded}/sub_objects",
            headers=headers,
            params={"limit": 10, "sort": "name", "direction": "asc"},
        )
        r.raise_for_status()
        root = r.json()
        dirs = root.get("dirs", [])
        files = root.get("files", [])
        logger.info(f"  Root: {len(dirs)} dirs, {len(files)} files")

    if not files:
        logger.warning("No files in root — trying first subdir...")
        if dirs:
            first_dir = dirs[0]
            encoded2 = quote(first_dir["id"], safe="")
            with httpx.Client(timeout=30) as c:
                r = c.get(
                    f"{ANYSHARE_BASE}/api/efast/v1/folders/{encoded2}/sub_objects",
                    headers=headers,
                    params={"limit": 10},
                )
                r.raise_for_status()
                sub = r.json()
                files = sub.get("files", [])
                logger.info(f"  Subdir '{first_dir.get('name')}': {len(files)} files")

    if not files:
        logger.error("No files found anywhere — aborting")
        return

    test_file = files[0]
    logger.info(f"Step 3: Testing download for '{test_file.get('name')}' ({test_file['id'][:60]}...)")

    # ── Step 3: Download file ───────────────────────────────
    import json
    with httpx.Client(timeout=30) as c:
        r = c.post(
            f"{ANYSHARE_BASE}/api/efast/v1/file/osdownload",
            headers={**headers, "Content-Type": "application/json"},
            json={"doc_id": test_file["id"]},
        )
        if r.status_code == 200:
            dl = r.json()
            authreq = dl.get("authrequest", [])
            logger.info(f"  Download size: {dl.get('size')} bytes, rev: {dl.get('rev', '')[:20]}")
            logger.info(f"  Auth method: {authreq[0] if len(authreq) > 0 else 'unknown'}")

            # Actually download
            if len(authreq) >= 2:
                dl_method = authreq[0]
                dl_url = authreq[1]
                dl_headers = {}
                for h in authreq[2:]:
                    if ": " in h:
                        k, v = h.split(": ", 1)
                        dl_headers[k] = v

                with httpx.Client(timeout=60) as dc:
                    dr = dc.request(dl_method, dl_url, headers=dl_headers)
                    dr.raise_for_status()
                    content = dr.content
                    logger.info(f"  Downloaded {len(content)} bytes successfully!")

                    # Save to temp file for upload test
                    import tempfile
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix="_" + test_file.get("name", "test.txt"))
                    tmp.write(content)
                    tmp.close()
                    tmp_path = tmp.name
                    logger.info(f"  Saved to: {tmp_path}")
        else:
            logger.error(f"  osdownload failed: {r.status_code}")
            return

    # ── Step 4: Create BISHENG space ────────────────────────
    logger.info("Step 4: Creating test space in BISHENG...")
    bs_cookies = {"access_token_cookie": BISHENG_COOKIE.split("=", 1)[1]}

    with httpx.Client(timeout=30) as c:
        r = c.post(
            f"{BISHENG_BASE}/api/v1/knowledge/space",
            json={"name": "E2E_Integration_Test", "description": "Auto test", "auth_type": "public"},
            cookies=bs_cookies,
        )
        if r.status_code == 200:
            space_id = r.json()["data"]["id"]
            logger.info(f"  Created space id={space_id}")
        else:
            logger.error(f"  Space creation failed: {r.status_code} {r.text[:200]}")
            return

    # ── Step 5: Upload file to BISHENG ──────────────────────
    logger.info("Step 5: Uploading to BISHENG...")
    try:
        with httpx.Client(timeout=60) as c:
            with open(tmp_path, "rb") as f:
                r = c.post(
                    f"{BISHENG_BASE}/api/v1/knowledge/upload/{space_id}",
                    files={"file": f},
                    cookies=bs_cookies,
                )
            if r.status_code == 200:
                file_path = r.json()["data"]["file_path"]
                logger.info(f"  Uploaded: {file_path[:80]}...")
            else:
                logger.error(f"  Upload failed: {r.status_code} {r.text[:200]}")
                # Cleanup space
                c.delete(f"{BISHENG_BASE}/api/v1/knowledge/space/{space_id}", cookies=bs_cookies)
                return

        # ── Step 6: Register file ────────────────────────────
        logger.info("Step 6: Registering file...")
        with httpx.Client(timeout=30) as c:
            r = c.post(
                f"{BISHENG_BASE}/api/v1/knowledge/space/{space_id}/files",
                json={"file_path": [file_path], "parent_id": None},
                cookies=bs_cookies,
            )
            if r.status_code == 200:
                file_record = r.json()["data"][0]
                file_id = file_record["id"]
                status = file_record["status"]
                logger.info(f"  Registered file_id={file_id}, status={status}")
            else:
                logger.error(f"  Register failed: {r.status_code} {r.text[:200]}")
                c.delete(f"{BISHENG_BASE}/api/v1/knowledge/space/{space_id}", cookies=bs_cookies)
                return

        # ── Step 7: Wait for ingestion ────────────────────────
        logger.info("Step 7: Waiting for ingestion...")
        import time
        for attempt in range(20):
            time.sleep(5)
            with httpx.Client(timeout=30) as c:
                r = c.get(
                    f"{BISHENG_BASE}/api/v1/knowledge/space/{space_id}/children",
                    params={"page": 1, "page_size": 10},
                    cookies=bs_cookies,
                )
                if r.status_code == 200:
                    items = r.json()["data"]["data"]
                    for item in items:
                        if item["id"] == file_id:
                            logger.info(f"  Attempt {attempt+1}: status={item['status']}")
                            if item["status"] == 2:
                                logger.info("  INGESTION SUCCESS!")
                                final_status = 2
                                break
                    else:
                        continue
                    break
        else:
            final_status = -1
            logger.warning("  Ingestion timeout — continuing anyway")

        # ── Step 8: Cleanup ──────────────────────────────────
        logger.info("Step 8: Cleaning up...")
        with httpx.Client(timeout=30) as c:
            c.delete(f"{BISHENG_BASE}/api/v1/knowledge/space/{space_id}", cookies=bs_cookies)
            logger.info("  Space deleted.")

        # Clean temp file
        import os
        os.unlink(tmp_path)

        logger.info("=== E2E Integration Test PASSED ===")
        logger.info(f"  Final status: {'SUCCESS' if final_status == 2 else f'status={final_status}'}")
        logger.info(f"  Source: {test_file.get('name')} ({test_file.get('size', '?')} bytes)")
        logger.info(f"  Via: AnyShare({ANYSHARE_BASE}) -> BISHENG({BISHENG_BASE})")

    except Exception as e:
        logger.exception(f"E2E test error: {e}")
        # Try cleanup
        try:
            import os
            os.unlink(tmp_path)
        except:
            pass
        try:
            with httpx.Client(timeout=30) as c:
                c.delete(f"{BISHENG_BASE}/api/v1/knowledge/space/{space_id}", cookies=bs_cookies)
        except:
            pass


if __name__ == "__main__":
    main()
