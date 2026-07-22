"""
个人库同步 — 正式版
用法: python sync_one_user.py <as_token> <bs_cookie> <as_lib_gns> <user_name>
"""
import sys, json, time, shutil, uuid, httpx, hashlib, datetime
from pathlib import Path
from urllib.parse import quote

AS = sys.argv[1] if len(sys.argv) > 1 else input("AnyShare Token: ").strip()
BS = sys.argv[2] if len(sys.argv) > 2 else input("BISHENG Cookie: ").strip()
LIB = sys.argv[3] if len(sys.argv) > 3 else input("AnyShare DocLib GNS: ").strip()
USER_NAME = sys.argv[4] if len(sys.argv) > 4 else input("User Name: ").strip()

AS_B = "https://5j-zsgl.powerchina.cn"
BS_B = "http://192.168.106.161:3001"

# 1. Delete old spaces for this user
print(f"=== Cleaning old '{USER_NAME}' spaces ===")
with httpx.Client(timeout=30) as c:
    r = c.get(f"{BS_B}/api/v1/knowledge/space/mine", cookies={"access_token_cookie": BS})
    for sp in r.json()["data"]:
        if USER_NAME in sp.get("name", ""):
            c.delete(f"{BS_B}/api/v1/knowledge/space/{sp['id']}", cookies={"access_token_cookie": BS})
            print(f"  Deleted: {sp['name']}")

# 2. Create space
print(f"\n=== Creating space: {USER_NAME} ===")
r = httpx.post(f"{BS_B}/api/v1/knowledge/space",
    json={"name": USER_NAME, "description": "AnyShare个人文档库", "auth_type": "public"},
    cookies={"access_token_cookie": BS})
SP = r.json()["data"]["id"]
print(f"  id={SP}")

# 3. Scan (BFS recursive)
print(f"\n=== Scanning (BFS recursive) ===")
all_files = []
all_dirs = []
queue = [(LIB, "")]
scanned = set()
while queue:
    gns, parent = queue.pop(0)
    if gns in scanned:
        continue
    scanned.add(gns)
    enc = quote(gns, safe="")
    r = httpx.get(f"{AS_B}/api/efast/v1/folders/{enc}/sub_objects?limit=200&sort=name&direction=asc",
        headers={"Authorization": f"Bearer {AS}"}, timeout=60)
    if r.status_code != 200:
        continue
    sub = r.json()
    for d in sub.get("dirs", []):
        all_dirs.append(d)
        queue.append((d["id"], gns))
    for f in sub.get("files", []):
        # Skip archive/compressed files (can't be parsed by BISHENG)
        if f["name"].lower().endswith((".zip", ".7z", ".rar", ".tar", ".gz")):
            continue
        f["_parent_gns"] = parent
        all_files.append(f)
    print(f"  Scanned: {len(all_dirs)} dirs, {len(all_files)} files", end="\r")
print(f"\n  Total: {len(all_dirs)} dirs, {len(all_files)} files")
for f in all_files[:10]:
    print(f"    {f['name'][:50]} ({f.get('size', 0)}b)")

files = all_files  # use all_files for transfer

# 4. Create folder structure
print(f"\n=== Creating folder structure ===")
folder_map = {}  # AnyShare GNS -> BISHENG folder_id
for d in sorted(all_dirs, key=lambda x: x["id"].count("/")):  # parents first
    nm = d["name"]
    parent_gns = d["id"].rsplit("/", 1)[0] if "/" in d["id"] else ""
    parent_id = folder_map.get(parent_gns, None)
    try:
        r = httpx.post(f"{BS_B}/api/v1/knowledge/space/{SP}/folders",
            json={"name": nm, "parent_id": parent_id},
            cookies={"access_token_cookie": BS})
        if r.status_code == 200:
            fid = r.json()["data"]["id"]
            folder_map[d["id"]] = fid
    except Exception as e:
        print(f"  Folder FAIL {nm}: {str(e)[:50]}")
print(f"  Created {len(folder_map)} folders")

# 5. Transfer
td = Path.home() / "AppData" / "Local" / "Temp" / "as_sync" / uuid.uuid4().hex[:8]
td.mkdir(parents=True, exist_ok=True)
ok = ng = 0
file_id_map = {}  # AnyShare GNS -> BISHENG file_id

for i, f in enumerate(all_files):
    nm, did = f["name"], f["id"]
    print(f"[{i+1}/{len(all_files)}] {nm[:50]}", end=" ", flush=True)
    try:
        # Download
        r = httpx.post(f"{AS_B}/api/efast/v1/file/osdownload",
            json={"docid": did, "rev": "", "authtype": "QUERY_STRING", "savename": nm, "usehttps": True},
            headers={"Authorization": f"Bearer {AS}"})
        a = r.json()["authrequest"]
        hh = {}
        for h in a[2:]:
            if ": " in h:
                k, v = h.split(": ", 1)
                hh[k] = v
        sf = "".join(c for c in nm if c.isalnum() or c in "._-()（）")
        lp = td / sf
        with httpx.Client(timeout=120) as cc:
            with cc.stream(a[0], a[1], headers=hh) as rr:
                rr.raise_for_status()
                with open(lp, "wb") as ff:
                    for ch in rr.iter_bytes(65536):
                        ff.write(ch)
        print(f"DL:{lp.stat().st_size}", end=" ", flush=True)

        # Upload
        with open(lp, "rb") as fh:
            r = httpx.post(f"{BS_B}/api/v1/knowledge/upload/{SP}",
                files={"file": fh},
                cookies={"access_token_cookie": BS})
        fp = r.json()["data"]["file_path"]

        # Register
        # Find parent folder: parent GNS = everything before last /
        parts = did.split("/")
        parent_gns = "/".join(parts[:-1]) if len(parts) > 3 else ""
        pfid = folder_map.get(did.rsplit("/", 1)[0] if "/" in did else "")
        # Try both formats
        if pfid is None:
            pfid = folder_map.get(parent_gns)
        r = httpx.post(f"{BS_B}/api/v1/knowledge/space/{SP}/files",
            json={"file_path": [fp], "parent_id": pfid},
            cookies={"access_token_cookie": BS})
        fid = r.json()["data"][0]["id"]
        file_id_map[did] = fid
        print(f"REG:{fid} STORED")
        ok += 1
        lp.unlink(missing_ok=True)
    except Exception as e:
        print(f"ERR:{str(e)[:80]}")
        ng += 1

shutil.rmtree(td, ignore_errors=True)
print(f"\n=== Transfer done: {ok}/{len(all_files)} OK, {ng} failed ===")

# ── Write mapping tables ────────────────────────────────────
print(f"\n=== Writing mapping records ===")
sys.path.insert(0, ".")
from app.models import init_db, get_session
from app.models.space_mapping import SyncSpaceMapping
from app.models.document_mapping import SyncDocumentMapping
from app.models.scan_run import SyncScanRun
from app.models.audit_event import SyncAuditEvent
from app.models.scope_config import SyncScopeConfig
from sqlmodel import select, func
import hashlib, datetime
init_db()

trace_id = uuid.uuid4().hex[:12]
now = datetime.datetime.now()

with get_session() as s:
    # 1. Scope config - check existing
    scope = s.exec(select(SyncScopeConfig).where(SyncScopeConfig.source_id == LIB)).first()
    if not scope:
        scope = SyncScopeConfig(
            tenant_id=1, source_type="knowledge_doc_lib",
            source_id=LIB, source_name=USER_NAME, enabled=True)
    s.add(scope)
    s.commit()

    # 2. Scan run
    scan = SyncScanRun(tenant_id=1, scan_type="manual", scope_config_id=scope.id,
        total_files=len(all_files), new_files=len(all_files), status="completed",
        started_at=now, completed_at=datetime.datetime.now())
    s.add(scan)
    s.commit()

    # 3. Space mapping - check existing first
    existing = s.exec(select(SyncSpaceMapping).where(SyncSpaceMapping.source_doc_lib_id == LIB)).first()
    if existing:
        sm = existing
        sm.target_space_id = SP
        sm.status = "created"
    else:
        sm = SyncSpaceMapping(tenant_id=1, source_doc_lib_id=LIB,
            source_doc_lib_name=USER_NAME, source_type="knowledge",
            target_space_id=SP, status="created")
    s.add(sm)
    s.commit()

    # 4. Document mappings - skip existing
    for f in all_files:
        if s.exec(select(SyncDocumentMapping).where(
                SyncDocumentMapping.source_doc_id == f["id"])).first():
            continue  # already synced in a previous run
        key = hashlib.sha256(f"{LIB}|{f['id']}|{f.get('rev','')}".encode()).hexdigest()[:32]
        dm = SyncDocumentMapping(tenant_id=1, space_mapping_id=sm.id,
            source_doc_id=f["id"], source_rev=f.get("rev",""),
            source_name=f["name"], source_size=f.get("size",0),
            content_version=f.get("rev",""),
            idempotency_key=key,
            status="succeeded", last_seen_scan_id=scan.id)
        s.add(dm)

    # 5. Audit
    s.add(SyncAuditEvent(tenant_id=1, trace_id=trace_id, action="sync",
        source_type="knowledge_doc_lib", source_id=LIB,
        target_type="knowledge_space", target_id=SP,
        operator="system", result="success",
        detail=f"Transferred {ok}/{len(all_files)} files"))
    s.commit()

from sqlmodel import select, func
with get_session() as s:
    scope_cnt = s.exec(select(func.count()).select_from(SyncScopeConfig)).one()
    scan_cnt = s.exec(select(func.count()).select_from(SyncScanRun)).one()
    space_cnt = s.exec(select(func.count()).select_from(SyncSpaceMapping)).one()
    doc_cnt = s.exec(select(func.count()).select_from(SyncDocumentMapping)).one()
    audit_cnt = s.exec(select(func.count()).select_from(SyncAuditEvent)).one()
    print(f"  DB: scope={scope_cnt} scan={scan_cnt} space={space_cnt} doc={doc_cnt} audit={audit_cnt}")

# ── Permission Sync (optimized: one ACL call per item, targeted user lookup) ──
print(f"\n=== Syncing ACL permissions ===")

# Translate AnyShare allow bits → BISHENG relation
def translate_relation(allows: set) -> str | None:
    if "download" not in allows:
        return None
    if allows >= {"display", "preview", "download", "modify", "create", "delete", "internal_sharing"}:
        return "manager"
    if allows >= {"display", "preview", "download", "modify", "create"}:
        return "editor"
    return "viewer"

# Step 1: Collect ACL for all items + cache results (one pass)
print(f"  Collecting ACL for {len(all_dirs)} dirs + {len(all_files)} files...")
acl_cache = {}  # any_gns -> perminfos
needed_users = set()  # unique display names to resolve (users)
needed_depts = set()  # unique department names to resolve

for any_gns in [d["id"] for d in all_dirs] + [f["id"] for f in all_files]:
    try:
        r = httpx.post(f"{AS_B}/api/eacp/v1/perm2/get",
            json={"docid": any_gns},
            headers={"Authorization": f"Bearer {AS}"})
        if r.status_code == 200:
            perms = r.json().get("perminfos", [])
            acl_cache[any_gns] = perms
            for p in perms:
                atype = p.get("accessortype", "user")
                aname = p.get("accessorname", "")
                if atype == "department":
                    needed_depts.add(aname)  # full name like "中国水利水电第五工程局有限公司"
                else:
                    parts = aname.split("/**eisoo**/")
                    display_name = parts[1] if len(parts) > 1 else parts[0]
                    if display_name:
                        needed_users.add(display_name)
    except Exception:
        pass
print(f"  Cached ACL for {len(acl_cache)} items, {len(needed_users)} users + {len(needed_depts)} depts to resolve")

# Step 2a: Resolve users by targeted keyword search (Chinese name prefix)
bs_user_map = {}  # display_name or external_id -> (user_id, type)
for display_name in needed_users:
    try:
        r = httpx.get(f"{BS_B}/api/v1/permissions/resources/knowledge_space/{SP}/grant-subjects/users",
            params={"keyword": display_name, "page": 1, "page_size": 5},
            cookies={"access_token_cookie": BS})
        for u in r.json().get("data", []):
            if u["user_name"] == display_name:
                bs_user_map[display_name] = (u["user_id"], "user")
                bs_user_map[u.get("external_id", "")] = (u["user_id"], "user")
                break
    except Exception:
        pass
print(f"  Resolved {len(needed_users)} users ({len(bs_user_map)//2} in map)")

# Step 2b: Resolve departments by name
bs_dept_map = {}  # dept_name -> dept_id
for dept_name in needed_depts:
    try:
        r = httpx.get(f"{BS_B}/api/v1/permissions/resources/knowledge_space/{SP}/grant-subjects/departments/search",
            params={"keyword": dept_name, "limit": 5},
            cookies={"access_token_cookie": BS})
        for root in r.json().get("data", {}).get("roots", []):
            def _find_matched(nodes, target):
                for n in nodes:
                    if n.get("matched") and n.get("name") == target:
                        return n["id"]
                    if n.get("children"):
                        r = _find_matched(n["children"], target)
                        if r:
                            return r
                return None
            did = _find_matched([root], dept_name)
            if did:
                bs_dept_map[dept_name] = did
                break
    except Exception:
        pass
print(f"  Resolved {len(bs_dept_map)}/{len(needed_depts)} departments")

# Step 3: Build resource list and authorize
synced_count = 0
acl_items = []  # (name, any_gns, bs_id, resource_type)
for d in all_dirs:
    fid = folder_map.get(d["id"])
    if fid:
        acl_items.append((d["name"], d["id"], fid, "folder"))
for f in all_files:
    fid = file_id_map.get(f["id"])
    if fid:
        acl_items.append((f["name"], f["id"], fid, "knowledge_file"))

for name, any_gns, bs_id, res_type in acl_items:
    perminfos = acl_cache.get(any_gns, [])
    if not perminfos:
        continue

    grants = []
    for p in perminfos:
        allows = set(p.get("allow", []))
        denys = set(p.get("deny", []))
        if denys:
            continue
        relation = translate_relation(allows)
        if relation is None:
            continue

        atype = p.get("accessortype", "user")
        aname = p.get("accessorname", "")

        if atype == "department":
            did = bs_dept_map.get(aname)
            if did:
                grants.append({"subject_type": "department", "subject_id": did,
                              "relation": relation, "include_children": True})
        else:
            parts = aname.split("/**eisoo**/")
            display_name = parts[1] if len(parts) > 1 else parts[0]
            found = bs_user_map.get(display_name) or bs_user_map.get(parts[0])
            if found:
                bs_uid, bs_stype = found
                grants.append({"subject_type": bs_stype, "subject_id": bs_uid, "relation": relation})

    if not grants:
        continue

    try:
        # authorize can be slow (OpenFGA writes), retry with 60s timeout
        for attempt in range(3):
            try:
                r = httpx.post(
                    f"{BS_B}/api/v1/permissions/resources/{res_type}/{bs_id}/authorize",
                    json={"grants": grants, "revokes": []},
                    cookies={"access_token_cookie": BS},
                    timeout=60)
                break
            except httpx.TimeoutException:
                if attempt < 2:
                    print(f"  retry {attempt+1}...", end=" ", flush=True)
                    time.sleep(3)
                else:
                    raise
        if r.status_code == 200:
            synced_count += 1
            print(f"  {res_type} {name[:35]}: {len(grants)} grants OK")
        else:
            print(f"  {res_type} {name[:35]}: FAIL({r.status_code}) {r.text[:100]}")
    except Exception as e:
        print(f"  ACL ERR {name[:30]}: {str(e)[:80]}")

print(f"\n=== DONE: Transfer={ok}/{len(all_files)}, ACL synced={synced_count}/{len(acl_items)} ===")
print(f"View: http://192.168.106.161:3001 → {USER_NAME} (id={SP})")
