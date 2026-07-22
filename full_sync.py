"""Full personal library sync — clean run.

Usage: python full_sync.py
"""
import sys, time, shutil, httpx, uuid
from pathlib import Path
sys.path.insert(0, ".")

from app.models import init_db, get_session
from app.models.task import SyncTask
from app.models.document_mapping import SyncDocumentMapping
from app.models.space_mapping import SyncSpaceMapping
from app.models.scan_run import SyncScanRun
from app.models.scope_config import SyncScopeConfig
from app.models.audit_event import SyncAuditEvent
from app.models.folder_mapping import SyncFolderMapping
from app.models.permission_snapshot import SyncPermissionSnapshot
from sqlmodel import text, select, func

# ── Config ────────────────────────────────────────────────
ANYSHARE_URL = "https://5j-zsgl.powerchina.cn"
AS_TOKEN = input("AnyShare OAuth Token: ").strip()
BISHENG_URL = "http://192.168.106.161:7860"
BS_COOKIE = input("BISHENG access_token_cookie value: ").strip()

DOCLIB_GNS = "gns://110F8E071F0243AEBDB4DFD59F52D131"
DOCLIB_NAME = "5jliming1_personal"

# ── 1. Clean up old data ──────────────────────────────────
print("=== Step 1: Clean old data ===")
init_db()
with get_session() as s:
    for t in ["anyshare_sync_audit_event","anyshare_sync_task","anyshare_sync_document_mapping",
              "anyshare_sync_space_mapping","anyshare_sync_scan_run","anyshare_sync_scope_config",
              "anyshare_sync_folder_mapping","anyshare_sync_permission_snapshot"]:
        s.exec(text(f"DELETE FROM {t}"))
    s.commit()

# Delete old BISHENG test spaces named "5jliming1"
print("=== Step 2: Clean old BISHENG spaces ===")
with httpx.Client(timeout=30) as c:
    r = c.get(f"{BISHENG_URL}/api/v1/knowledge/space/mine", cookies={"access_token_cookie": BS_COOKIE})
    spaces = r.json()["data"]
    for sp in spaces:
        if "5jliming1" in sp.get("name", ""):
            print(f"  Deleting space id={sp['id']} name={sp['name']}")
            c.delete(f"{BISHENG_URL}/api/v1/knowledge/space/{sp['id']}", cookies={"access_token_cookie": BS_COOKIE})

# ── 3. Scan AnyShare ──────────────────────────────────────
print("=== Step 3: Scan AnyShare personal lib ===")
from urllib.parse import quote
all_files = []
root = DOCLIB_GNS
queue = [(root, 0)]
while queue:
    gns, depth = queue.pop(0)
    if depth > 5: continue
    encoded = quote(gns, safe="")
    r = httpx.get(
        f"{ANYSHARE_URL}/api/efast/v1/folders/{encoded}/sub_objects",
        headers={"Authorization": f"Bearer {AS_TOKEN}"},
        params={"limit": 100, "sort": "name", "direction": "asc"},
    )
    r.raise_for_status()
    page = r.json()
    for d in page.get("dirs", []):
        queue.append((d["id"], depth + 1))
    for f in page.get("files", []):
        all_files.append(f)
        print(f"  [{len(all_files)}] {f['name']} ({f.get('size',0)} bytes)")

print(f"Total files: {len(all_files)}")

# ── 4. Create BISHENG space ───────────────────────────────
print("=== Step 4: Create BISHENG space ===")
space_name = f"{DOCLIB_NAME}_{uuid.uuid4().hex[:6]}"
r = httpx.post(
    f"{BISHENG_URL}/api/v1/knowledge/space",
    json={"name": space_name, "description": "Full sync test", "auth_type": "public"},
    cookies={"access_token_cookie": BS_COOKIE},
)
r.raise_for_status()
SPACE_ID = r.json()["data"]["id"]
print(f"  Space: {space_name} (id={SPACE_ID})")

# ── 5. Download + Upload + Register + Track for EACH file ─
print("=== Step 5: Transfer all files ===")
temp_dir = Path.home() / "AppData" / "Local" / "Temp" / "anyshare-sync" / uuid.uuid4().hex[:8]
temp_dir.mkdir(parents=True, exist_ok=True)

success = 0
failed = 0

for fi, file_info in enumerate(all_files):
    name = file_info["name"]
    docid = file_info["id"]
    print(f"\n[{fi+1}/{len(all_files)}] {name}")

    try:
        # 5a: Download
        r = httpx.post(
            f"{ANYSHARE_URL}/api/efast/v1/file/osdownload",
            headers={"Authorization": f"Bearer {AS_TOKEN}", "Content-Type": "application/json"},
            json={"docid": docid, "rev": "", "authtype": "QUERY_STRING", "savename": name, "usehttps": True},
        )
        r.raise_for_status()
        dl = r.json()
        authreq = dl["authrequest"]
        dl_method, dl_url = authreq[0], authreq[1]
        dl_headers = {}
        for h in authreq[2:]:
            if ": " in h:
                k, v = h.split(": ", 1)
                dl_headers[k] = v

        safe_name = "".join(c for c in name if c.isalnum() or c in "._-() （）")
        local_path = temp_dir / safe_name
        with httpx.Client(timeout=120) as c:
            with c.stream(dl_method, dl_url, headers=dl_headers) as resp:
                resp.raise_for_status()
                with open(local_path, "wb") as f:
                    for chunk in resp.iter_bytes(65536):
                        f.write(chunk)
        print(f"  Download: {local_path.stat().st_size} bytes")

        # 5b: Upload to BISHENG
        with open(local_path, "rb") as f:
            r = httpx.post(f"{BISHENG_URL}/api/v1/knowledge/upload/{SPACE_ID}",
                           files={"file": f}, cookies={"access_token_cookie": BS_COOKIE})
        r.raise_for_status()
        file_path = r.json()["data"]["file_path"]

        # 5c: Register
        r = httpx.post(f"{BISHENG_URL}/api/v1/knowledge/space/{SPACE_ID}/files",
                       json={"file_path": [file_path], "parent_id": None},
                       cookies={"access_token_cookie": BS_COOKIE})
        r.raise_for_status()
        file_id = r.json()["data"][0]["id"]
        print(f"  Registered: file_id={file_id}")

        # 5d: Wait for ingestion
        for wait in [5, 10, 15, 20, 30, 30]:
            time.sleep(wait)
            r = httpx.get(f"{BISHENG_URL}/api/v1/knowledge/space/{SPACE_ID}/children",
                          params={"page": 1, "page_size": 200},
                          cookies={"access_token_cookie": BS_COOKIE})
            if r.status_code == 200:
                items = r.json()["data"]["data"]
                for item in items:
                    if item["id"] == file_id:
                        if item["status"] == 2:
                            print(f"  Ingestion: SUCCESS")
                            success += 1
                            break
                        elif item["status"] in (3, 7):
                            print(f"  Ingestion: FAILED (status={item['status']})")
                            failed += 1
                            break
                else:
                    continue
                break
        else:
            print(f"  Ingestion: TIMEOUT")
            failed += 1

        local_path.unlink(missing_ok=True)

    except Exception as e:
        print(f"  ERROR: {e}")
        failed += 1

# Cleanup
shutil.rmtree(temp_dir, ignore_errors=True)

print(f"\n=== DONE ===")
print(f"  Success: {success}/{len(all_files)}")
print(f"  Failed:  {failed}/{len(all_files)}")
print(f"  BISHENG space: {space_name} (id={SPACE_ID})")
print(f"  View at: http://192.168.106.161:3001")
