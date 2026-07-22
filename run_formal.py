"""Integration test: run formal modules end-to-end, mirroring sync_one_user.py flow.
Usage: python run_formal.py <as_token> <bs_cookie> <as_lib_gns> <space_name>
"""
import sys, logging, uuid, hashlib, datetime, json
from pathlib import Path
from urllib.parse import quote
import httpx

# Args
AS_T = sys.argv[1] if len(sys.argv) > 1 else input("AnyShare Token: ").strip()
BS_C = sys.argv[2] if len(sys.argv) > 2 else input("BISHENG Cookie: ").strip()
LIB_GNS = sys.argv[3] if len(sys.argv) > 3 else input("AnyShare DocLib GNS: ").strip()
SPACE_NAME = sys.argv[4] if len(sys.argv) > 4 else input("Space Name: ").strip()

AS_B = "https://5j-zsgl.powerchina.cn"
BS_B = "http://192.168.106.161:3001"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Init modules ─────────────────────────────────────────────
from app.connectors.bisheng.client import BishengClient
from app.connectors.bisheng.space import BishengSpace
from app.connectors.bisheng.folder import BishengFolder
from app.connectors.bisheng.file_transfer import BishengFileTransfer
from app.connectors.bisheng.permission import BishengPermission
from app.services.principal_mapper import PrincipalMapper, parse_accessorname
from app.models import init_db, get_session
from app.models.space_mapping import SyncSpaceMapping
from app.models.document_mapping import SyncDocumentMapping
from app.models.scan_run import SyncScanRun
from app.models.audit_event import SyncAuditEvent
from app.models.scope_config import SyncScopeConfig
from sqlmodel import select, func

bs = BishengClient(BS_B, BS_C, timeout=60.0)
bs_space = BishengSpace(bs)
bs_folder = BishengFolder(bs)
bs_file = BishengFileTransfer(bs)
bs_perm = BishengPermission(bs)
mapper = PrincipalMapper()
mapper.set_api_context(bs_perm, None)  # set space_id after creation

# ── 0. Clean old spaces ───────────────────────────────────────
print(f"\n=== Cleaning old '{SPACE_NAME}' spaces ===")
bs_space.cleanup_by_name(SPACE_NAME)

# ── 1. Create space ───────────────────────────────────────────
print(f"\n=== Creating space: {SPACE_NAME} ===")
SP = bs_space.create_personal(SPACE_NAME, "AnyShare知识库迁移")
mapper.set_api_context(bs_perm, SP)
print(f"  id={SP}")

# ── 2. Scan (BFS recursive — using bare httpx for now, module path is same) ──
print(f"\n=== Scanning (BFS recursive) ===")
all_files = []
all_dirs = []
SKIP_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".iso"}
queue = [(LIB_GNS, "")]
scanned = set()
while queue:
    gns, parent = queue.pop(0)
    if gns in scanned:
        continue
    scanned.add(gns)
    enc = quote(gns, safe="")
    r = httpx.get(f"{AS_B}/api/efast/v1/folders/{enc}/sub_objects?limit=200&sort=name&direction=asc",
        headers={"Authorization": f"Bearer {AS_T}"}, timeout=60)
    if r.status_code != 200:
        continue
    sub = r.json()
    for d in sub.get("dirs", []):
        all_dirs.append(d)
        queue.append((d["id"], gns))
    for f in sub.get("files", []):
        if f.get("name", "").lower().endswith(tuple(SKIP_EXT)):
            continue
        f["_parent_gns"] = parent
        all_files.append(f)
    print(f"  Scanned: {len(all_dirs)} dirs, {len(all_files)} files", end="\r")
print(f"\n  Total: {len(all_dirs)} dirs, {len(all_files)} files")
for f in all_files[:10]:
    print(f"    {f['name'][:50]} ({f.get('size', 0)}b)")

# ── 3. Create folder structure ──────────────────────────────
print(f"\n=== Creating folder structure ===")
folder_map = {}  # AnyShare GNS -> BISHENG folder_id
for d in sorted(all_dirs, key=lambda x: x["id"].count("/")):
    nm = d["name"]
    parent_id = None
    parent_gns = d["id"].rsplit("/", 1)[0] if "/" in d["id"] else ""
    if parent_gns and parent_gns in folder_map:
        parent_id = folder_map[parent_gns]
    try:
        fid = bs_folder.create(SP, nm, parent_id=parent_id)
        folder_map[d["id"]] = fid
    except Exception as e:
        print(f"  Folder FAIL {nm}: {str(e)[:50]}")
print(f"  Created {len(folder_map)} folders")

# ── 4. Transfer files ──────────────────────────────────────
print(f"\n=== Transferring files ===")
td = Path.home() / "AppData" / "Local" / "Temp" / "as_sync" / uuid.uuid4().hex[:8]
td.mkdir(parents=True, exist_ok=True)
ok = ng = 0
file_id_map = {}

for i, f in enumerate(all_files):
    nm, did = f["name"], f["id"]
    print(f"[{i+1}/{len(all_files)}] {nm[:50]}", end=" ", flush=True)
    try:
        # Download
        r = httpx.post(f"{AS_B}/api/efast/v1/file/osdownload",
            json={"docid": did, "rev": "", "authtype": "QUERY_STRING", "savename": nm, "usehttps": True},
            headers={"Authorization": f"Bearer {AS_T}"})
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
        fp = bs_file.upload_to_minio(SP, lp)

        # Register
        parent_gns = did.rsplit("/", 1)[0] if "/" in did else ""
        pfid = folder_map.get(parent_gns)
        reg_result = bs_file.register(SP, fp, parent_id=pfid)
        fid = reg_result["id"] if isinstance(reg_result, dict) else reg_result[0]["id"]
        file_id_map[did] = fid
        print(f"REG:{fid} STORED")
        ok += 1
        lp.unlink(missing_ok=True)
    except Exception as e:
        print(f"ERR:{str(e)[:80]}")
        ng += 1

import shutil
shutil.rmtree(td, ignore_errors=True)
print(f"\n=== Transfer done: {ok}/{len(all_files)} OK, {ng} failed ===")

# ── 5. Write mapping tables ──────────────────────────────────
print(f"\n=== Writing mapping records ===")
init_db()

trace_id = uuid.uuid4().hex[:12]
now = datetime.datetime.now()

with get_session() as s:
    scope = s.exec(select(SyncScopeConfig).where(SyncScopeConfig.source_id == LIB_GNS)).first()
    if not scope:
        scope = SyncScopeConfig(tenant_id=1, source_type="knowledge_doc_lib",
            source_id=LIB_GNS, source_name=SPACE_NAME, enabled=True)
    s.add(scope)
    s.commit()

    scan = SyncScanRun(tenant_id=1, scan_type="manual", scope_config_id=scope.id,
        total_files=len(all_files), new_files=len(all_files), status="completed",
        started_at=now, completed_at=datetime.datetime.now())
    s.add(scan)
    s.commit()

    existing = s.exec(select(SyncSpaceMapping).where(SyncSpaceMapping.source_doc_lib_id == LIB_GNS)).first()
    if existing:
        sm = existing
        sm.target_space_id = SP
        sm.status = "created"
    else:
        sm = SyncSpaceMapping(tenant_id=1, source_doc_lib_id=LIB_GNS,
            source_doc_lib_name=SPACE_NAME, source_type="knowledge",
            target_space_id=SP, status="created")
    s.add(sm)
    s.commit()

    for f in all_files:
        if s.exec(select(SyncDocumentMapping).where(
                SyncDocumentMapping.source_doc_id == f["id"])).first():
            continue
        key = hashlib.sha256(f"{LIB_GNS}|{f['id']}|{f.get('rev','')}".encode()).hexdigest()[:32]
        dm = SyncDocumentMapping(tenant_id=1, space_mapping_id=sm.id,
            source_doc_id=f["id"], source_rev=f.get("rev", ""),
            source_name=f["name"], source_size=f.get("size", 0),
            content_version=f.get("rev", ""), idempotency_key=key,
            status="succeeded", last_seen_scan_id=scan.id)
        s.add(dm)

    s.add(SyncAuditEvent(tenant_id=1, trace_id=trace_id, action="sync",
        source_type="knowledge_doc_lib", source_id=LIB_GNS,
        target_type="knowledge_space", target_id=SP,
        operator="system", result="success",
        detail=f"Transferred {ok}/{len(all_files)} files"))
    s.commit()

    scope_cnt = s.exec(select(func.count()).select_from(SyncScopeConfig)).one()
    scan_cnt = s.exec(select(func.count()).select_from(SyncScanRun)).one()
    space_cnt = s.exec(select(func.count()).select_from(SyncSpaceMapping)).one()
    doc_cnt = s.exec(select(func.count()).select_from(SyncDocumentMapping)).one()
    audit_cnt = s.exec(select(func.count()).select_from(SyncAuditEvent)).one()
    print(f"  DB: scope={scope_cnt} scan={scan_cnt} space={space_cnt} doc={doc_cnt} audit={audit_cnt}")

# ── 6. Permission Sync (using formal modules!) ──────────────
print(f"\n=== Syncing ACL permissions (via formal modules) ===")

# Collect ACL for all items
print(f"  Collecting ACL for {len(all_dirs)} dirs + {len(all_files)} files...")
acl_cache = {}
needed_users = set()
needed_depts = set()

for any_gns in [d["id"] for d in all_dirs] + [f["id"] for f in all_files]:
    try:
        r = httpx.post(f"{AS_B}/api/eacp/v1/perm2/get",
            json={"docid": any_gns},
            headers={"Authorization": f"Bearer {AS_T}"}, timeout=60)
        if r.status_code == 200:
            perms = r.json().get("perminfos", [])
            acl_cache[any_gns] = perms
            for p in perms:
                atype = p.get("accessortype", "user")
                aname = p.get("accessorname", "")
                if atype == "department":
                    needed_depts.add(aname)
                else:
                    uname, dname = parse_accessorname(aname)
                    if dname:
                        needed_users.add(dname)
                    if uname:
                        needed_users.add(uname)
    except Exception as e:
        print(f"  ACL ERR: {e}")
print(f"  Cached {len(acl_cache)} items, {len(needed_users)} users + {len(needed_depts)} depts")

# Resolve users via Permission module
print(f"  Resolving principals...")
for display_name in needed_users:
    try:
        uid = mapper.resolve_principal(display_name if display_name else "", "user")
        if uid:
            print(f"    {display_name} -> uid={uid}")
    except Exception:
        pass

for dept_name in needed_depts:
    try:
        did = mapper.resolve_principal(dept_name, "department")
        if did:
            print(f"    [{dept_name}] -> did={did}")
    except Exception:
        pass
print(f"  Mapped users: {mapper.get_mapped_count()}")

# Translate + Authorize per resource
synced_count = 0
acl_items = []
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

        # Determine relation
        if "download" not in allows:
            continue
        relation = "viewer"
        if allows >= {"display", "preview", "download", "modify", "create", "delete", "internal_sharing"}:
            relation = "manager"
        elif allows >= {"display", "preview", "download", "modify", "create"}:
            relation = "editor"

        atype = p.get("accessortype", "user")
        aname = p.get("accessorname", "")

        if atype == "department":
            did = mapper.resolve_principal(aname, "department")
            if did:
                # FIXME: include_children=True causes MySQL "Too many connections"
                # when dept has 50+ sub-depts. Enable after increasing max_connections.
                grants.append({"subject_type": "department", "subject_id": did,
                              "relation": relation, "include_children": False})
        else:
            uname, dname = parse_accessorname(aname)
            # Resolve by display name first, then username
            target = mapper.resolve_principal(dname or uname, "user")
            if target:
                grants.append({"subject_type": "user", "subject_id": target, "relation": relation})

    if not grants:
        continue

    ok_result = bs_perm.authorize(res_type, bs_id, grants=grants, timeout=60, retries=2)
    if ok_result:
        synced_count += 1
        print(f"  {res_type} {name[:35]}: {len(grants)} grants OK")
    else:
        print(f"  {res_type} {name[:35]}: FAIL")

print(f"\n=== DONE: Transfer={ok}/{len(all_files)}, ACL synced={synced_count}/{len(acl_items)} ===")
print(f"View: http://192.168.106.161:3001 → {SPACE_NAME} (id={SP})")
