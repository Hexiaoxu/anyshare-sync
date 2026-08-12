"""Sync pipeline — runs full migration for one doc lib scope.
Supports: knowledge_doc_lib, department_doc_lib, user_doc_lib.
"""

from __future__ import annotations

import logging, uuid, hashlib, datetime, json, shutil
from pathlib import Path
from urllib.parse import quote
from typing import Callable

import httpx

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

logger = logging.getLogger(__name__)

# File extensions to skip (archives — BISHENG can't parse)
SKIP_EXTENSIONS = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".iso"}


class SyncPipeline:
    """Runs full migration for one AnyShare doc lib → BISHENG space.

    Usage:
        pipeline = SyncPipeline(
            bs_base="http://192.168.106.161:3001",
            bs_cookie="eyJ...",
            as_base="https://5j-zsgl.powerchina.cn",
            as_token="ory_at...",
        )
        result = pipeline.run(
            lib_gns="gns://...",
            space_name="公司资质_v1",
            ancestors=None,  # optional: ["公司总部", "人力资源部"]
            skip_download=False,  # True for department doc libs without download token
        )
    """

    def __init__(self, bs_base: str, bs_cookie: str,
                 as_base: str, as_token: str,
                 as_auth=None, as_account: str = None):
        # Tokens — auth object for auto-refresh, raw token as fallback
        self._as_token = as_token
        self._as_base = as_base.rstrip("/")
        self._as_auth = as_auth  # AnyShareAuth instance (optional)
        self._as_account = as_account  # username for auto token (e.g. "5j_lim")

        # BISHENG clients
        self._bs = BishengClient(bs_base, bs_cookie, timeout=60)
        self._bs_space = BishengSpace(self._bs)
        self._bs_folder = BishengFolder(self._bs)
        self._bs_file = BishengFileTransfer(self._bs)
        self._bs_perm = BishengPermission(self._bs)
        self._mapper = PrincipalMapper()
        self._init_state()

    def _get_as_token(self) -> str:
        """Get AnyShare token — auto-refresh via user account if available."""
        if self._as_auth and self._as_account:
            try:
                return self._as_auth.get_user_token(self._as_account)
            except Exception:
                pass
        return self._as_token

    # State — initialized here so incremental sync works without run()
    def _init_state(self):
        if not hasattr(self, '_space_id'):
            self._space_id: int | None = None
        if not hasattr(self, '_folder_map'):
            self._folder_map: dict[str, int] = {}
        if not hasattr(self, '_file_map'):
            self._file_map: dict[str, int] = {}
        if not hasattr(self, '_uuid_to_gns'):
            self._uuid_to_gns: dict[str, str] = {}
        if not hasattr(self, '_gns_to_name'):
            self._gns_to_name: dict[str, str] = {}
        if not hasattr(self, '_bs_folder_by_path'):
            self._bs_folder_by_path: dict[str, int] = {}

    # ── Main entry ──────────────────────────────────────────

    def run(self, lib_gns: str, space_name: str,
            ancestors: list[str] | None = None,
            skip_download: bool = False,
            source_type: str = "knowledge_doc_lib",
            incremental: bool = False,
            grant_owner: str | None = None,
            no_root_perms: bool = False) -> dict:
        """Run full sync pipeline. Returns summary dict.

        Args:
            incremental: If True, reuse existing space and only sync
                         new/changed files. Skips cleanup.
            grant_owner: BISHENG username (external_id or display name)
                         to grant as additional owner of the space.
                         Used for personal lib migrations.
        """
        trace_id = uuid.uuid4().hex[:12]
        from app.logger import set_trace_id
        set_trace_id(trace_id)
        start_time = datetime.datetime.now()

        logger.info(f"=== Sync start: {space_name} ({source_type}) "
                     f"{'incremental' if incremental else 'full'} ===")

        try:
            # 0. Space setup
            if incremental:
                self._space_id = self._find_or_create_space(space_name)
            else:
                self._cleanup_old(space_name)
                self._space_id = self._bs_space.create_personal(
                    space_name, "AnyShare文档迁移")

            self._mapper.set_api_context(self._bs_perm, self._space_id)
            logger.info(f"Space: id={self._space_id}")

            # Grant target user as owner (for personal lib migration)
            if grant_owner:
                target_uid = self._mapper.resolve_principal(grant_owner, "user")
                if target_uid:
                    self._bs_perm.authorize(
                        "knowledge_space", self._space_id,
                        grants=[{"subject_type": "user", "subject_id": target_uid,
                                 "relation": "owner"}],
                        timeout=60, retries=2)
                    logger.info(f"Granted owner: {grant_owner} -> uid={target_uid}")
                else:
                    logger.warning(f"Owner grant failed: {grant_owner} not found in BISHENG")

            # 1b. Create ancestor folders (skip if incremental and already exists)
            ancestor_parent = None
            if ancestors:
                for name in ancestors:
                    existing_id = self._find_folder_by_name(name, ancestor_parent)
                    if existing_id:
                        ancestor_parent = existing_id
                        logger.info(f"Ancestor folder (reuse): {name} (id={ancestor_parent})")
                    else:
                        ancestor_parent = self._bs_folder.create(
                            self._space_id, name, parent_id=ancestor_parent)
                        logger.info(f"Ancestor folder: {name} (id={ancestor_parent})")

            # 2. Scan
            all_dirs, all_files, skipped = self._scan(lib_gns)
            logger.info(f"Scan: {len(all_dirs)} dirs, {len(all_files)} files"
                        + (f", {skipped} skipped" if skipped else ""))

            # 3. Create folder structure
            self._create_folders(all_dirs, lib_gns, ancestor_parent)
            logger.info(f"Folders: {len(self._folder_map)} created")

            # 4. Transfer files (incremental: skip existing)
            transfer_files = all_files
            skipped_existing = 0
            if incremental:
                transfer_files, skipped_existing = self._filter_new_files(
                    all_files, lib_gns)
                logger.info(f"Incremental: {skipped_existing} unchanged, "
                            f"{len(transfer_files)} new/changed to transfer")

            ok, ng = 0, 0
            if not skip_download and transfer_files:
                ok, ng = self._transfer_files(transfer_files, lib_gns, ancestor_parent)
                logger.info(f"Transfer: {ok}/{len(transfer_files)} OK, {ng} failed")

            # 5. Write mapping tables
            scan_id = self._write_mappings(lib_gns, space_name, source_type,
                                           all_files, ok, trace_id, start_time)

            # 6. Sync permissions
            synced, total = self._sync_permissions(
                all_dirs, all_files, lib_gns, ancestor_parent,
                no_root_perms=no_root_perms)

            elapsed = (datetime.datetime.now() - start_time).total_seconds()
            result = {
                "space_id": self._space_id,
                "space_name": space_name,
                "dirs": len(all_dirs),
                "files": len(all_files),
                "transferred": ok,
                "failed": ng,
                "skipped_archives": skipped,
                "acl_synced": f"{synced}/{total}",
                "elapsed_sec": elapsed,
                "scan_id": scan_id,
            }
            logger.info(f"=== Sync done ({elapsed:.0f}s): "
                         f"{len(all_dirs)}D/{len(all_files)}F, "
                         f"xfer={ok}/{len(all_files)}, ACL={synced}/{total} ===")
            return result

        except Exception:
            logger.exception(f"Sync failed: {space_name}")
            return {"error": "see error.log for details", "space_id": self._space_id}

    # ── Stages ──────────────────────────────────────────────

    def _cleanup_old(self, name: str):
        deleted = self._bs_space.cleanup_by_name(name)
        if deleted:
            logger.info(f"Cleaned {deleted} old spaces matching '{name}'")

    @staticmethod
    def _extract_uuid(gns: str) -> str:
        """Extract UUID from GNS path: 'gns://.../FAA1B87280EE4000B306A201B0ECF826' -> 'FAA1B87280EE4000B306A201B0ECF826'"""
        return gns.rsplit("/", 1)[-1]

    def resolve_uuid(self, obj_id: str) -> str | None:
        """Resolve objId UUID to full GNS path."""
        if not hasattr(self, '_uuid_to_gns'):
            self._uuid_to_gns = {}
        return self._uuid_to_gns.get(obj_id)

    def sync_from_logs(self, console_token: str, since_date: int,
                       until_date: int) -> dict:
        """Incremental sync: pull Console EACPLog, find changed items, re-sync permissions.

        Args:
            console_token: Console OAuth token (from console.oauth2_token cookie)
            since_date: Start timestamp in microseconds
            until_date: End timestamp in microseconds
        """
        import httpx
        logger.info("=== Incremental sync from console logs ===")

        # 1. Pull logs
        changed_uuids = set()
        for logType in [12]:  # document operations
            start = 0
            while True:
                body = [{'ncTGetPageLogParam': {
                    'userId': '3e7a9110-3de5-11ef-bb23-de677a88534a',
                    'start': start, 'limit': 500,
                    'maxLogId': 9223372036854775807,
                    'logType': logType,
                    'levels': [], 'macs': [], 'ips': [], 'displayNames': [],
                    'opTypes': [2, 11, 19, 22],  # upload, perm change, modify, new folder
                    'msgs': [], 'exMsgs': [],
                    'startDate': since_date,
                    'endDate': until_date
                }}]
                try:
                    r = httpx.post(
                        f"{self._as_base}/console/api/EACPLog/GetPageLog",
                        json=body, timeout=30,
                        headers={'Authorization': f'Bearer {console_token}',
                                 'Content-Type': 'application/json;charset=UTF-8'})
                    if r.status_code != 200 or not r.json():
                        break
                    for entry in r.json():
                        obj_id = entry.get('objId', '')
                        if obj_id:
                            changed_uuids.add(obj_id)
                    start += 500
                    if len(r.json()) < 500:
                        break
                except Exception as e:
                    logger.warning(f"Log pull error: {e}")
                    break

        logger.info(f"Found {len(changed_uuids)} changed UUIDs in logs")

        # 2. Resolve UUIDs to GNS and re-sync permissions
        synced = 0
        not_found = 0
        for obj_id in changed_uuids:
            gns = self.resolve_uuid(obj_id)
            if not gns:
                not_found += 1
                continue

            # Determine resource type and BISHENG ID
            bs_id = self._folder_map.get(gns) or self._file_map.get(gns)
            if not bs_id:
                continue
            res_type = "folder" if gns in self._folder_map else "knowledge_file"
            name = self._gns_to_name.get(gns, obj_id[:12])

            # Get ACL and re-authorize
            try:
                r = httpx.post(
                    f"{self._as_base}/api/eacp/v1/perm2/get",
                    json={"docid": gns},
                    headers={"Authorization": f"Bearer {self._get_as_token()}"},
                    timeout=30)
                if r.status_code != 200:
                    continue
                grants = self._build_grants(r.json().get("perminfos", []))
                if grants:
                    ok = self._bs_perm.authorize(res_type, bs_id, grants=grants,
                                                 timeout=60, retries=2)
                    if ok:
                        synced += 1
                        logger.debug(f"  incr sync: {res_type} {name[:40]} OK")
            except Exception as e:
                logger.warning(f"  incr sync failed: {obj_id} — {e}")

        logger.info(f"Incremental sync done: {synced} synced, {not_found} not found in mapping")
        return {"synced": synced, "not_found": not_found,
                "total_changes": len(changed_uuids)}

    def _build_grants(self, perminfos: list) -> list[dict]:
        """Build BISHENG grants from AnyShare perm2/get response.

        Rule: any ACL entry with 'download' in allow (and no deny) → viewer.
        """
        grants = []
        for p in perminfos:
            allows = set(p.get("allow", []))
            denys = set(p.get("deny", []))
            if denys or "download" not in allows:
                continue

            atype = p.get("accessortype", "user")
            aname = p.get("accessorname", "")
            if atype == "department":
                did = self._mapper.resolve_principal(aname, "department")
                if did:
                    grants.append({"subject_type": "department", "subject_id": did,
                                   "relation": "viewer", "include_children": False})
            else:
                from app.services.principal_mapper import parse_accessorname
                uname, dname = parse_accessorname(aname)
                uid = self._mapper.resolve_principal(dname or uname, "user")
                if uid:
                    grants.append({"subject_type": "user", "subject_id": uid,
                                   "relation": "viewer"})
        return grants

    def _build_grants_for_gns(self, gns: str) -> list[dict]:
        """Fetch ACL from AnyShare for a specific GNS and build BISHENG grants."""
        try:
            r = httpx.post(
                f"{self._as_base}/api/eacp/v1/perm2/get",
                json={"docid": gns},
                headers={"Authorization": f"Bearer {self._get_as_token()}"},
                timeout=30)
            if r.status_code == 200:
                return self._build_grants(r.json().get("perminfos", []))
        except Exception as e:
            logger.warning(f"_build_grants_for_gns failed for {gns}: {e}")
        return []

    def _find_folder_by_name(self, name: str, parent_id: int | None) -> int | None:
        """Find existing folder by name under a parent. Returns folder_id or None."""
        try:
            children = self._bs._get(f"/api/v1/knowledge/space/{self._space_id}/children",
                                     params={"page": 1, "page_size": 200})
            data = self._bs.ok(children).get("data", {})
            for item in data.get("data", []):
                if (item.get("file_type") == 0  # DIR
                        and item.get("file_name") == name
                        and item.get("parent_id") == parent_id):
                    return item["id"]
        except Exception:
            pass
        return None

    def _find_or_create_space(self, name: str) -> int:
        """Find existing space by name, or create a new one."""
        for sp in self._bs_space.list_mine():
            if sp.get("name") == name:
                logger.info(f"Reusing existing space: id={sp['id']}")
                return sp["id"]
        return self._bs_space.create_personal(name, "AnyShare文档迁移")

    def _scan(self, lib_gns: str, max_depth: int = 6,
              max_dirs: int = 500, max_files: int = 2000) -> tuple[list, list, int]:
        """BFS scan with marker pagination. Returns (all_dirs, all_files, skipped_count)."""
        all_dirs, all_files = [], []
        skipped = 0
        # Ensure attributes exist (defensive against stale pyc)
        if not hasattr(self, '_uuid_to_gns'):
            self._uuid_to_gns = {}
        if not hasattr(self, '_gns_to_name'):
            self._gns_to_name = {}
        if not hasattr(self, '_file_map'):
            self._file_map = {}
        queue = [(lib_gns, "", 0)]  # (gns, parent, depth)
        scanned = set()

        while queue:
            gns, parent, depth = queue.pop(0)
            if gns in scanned:
                continue
            scanned.add(gns)

            # Depth limit
            if depth > max_depth:
                continue

            # Marker pagination
            marker = None
            while True:
                url = (f"{self._as_base}/api/efast/v1/folders/{quote(gns, safe='')}"
                       f"/sub_objects?limit=200&sort=name&direction=asc"
                       f"&permission_attributes_required=false")
                if marker:
                    url += f"&marker={marker}"
                # Retry on transient network errors
                for retry in range(3):
                    try:
                        r = httpx.get(url,
                            headers={"Authorization": f"Bearer {self._get_as_token()}"},
                            timeout=60)
                        break
                    except (httpx.ConnectError, httpx.RemoteProtocolError,
                            httpx.ReadTimeout) as e:
                        if retry < 2:
                            logger.debug(f"Scan retry {retry+1} for {url[-40:]}: {e}")
                            import time as _time
                            _time.sleep(5 * (retry + 1))
                        else:
                            raise
                if r.status_code != 200:
                    if r.status_code == 429:  # rate limited
                        import time as _t
                        _t.sleep(10)
                        continue
                    break

                sub = r.json()
                for d in sub.get("dirs", []):
                    if len(all_dirs) >= max_dirs:
                        break
                    all_dirs.append(d)
                    # Map UUID -> GNS
                    self._uuid_to_gns[self._extract_uuid(d["id"])] = d["id"]
                    self._gns_to_name[d["id"]] = d.get("name", "")
                    queue.append((d["id"], gns, depth + 1))
                for f in sub.get("files", []):
                    if len(all_files) >= max_files:
                        break
                    if f.get("name", "").lower().endswith(tuple(SKIP_EXTENSIONS)):
                        skipped += 1
                        continue
                    f["_parent_gns"] = parent
                    all_files.append(f)
                    # Map UUID -> GNS
                    self._uuid_to_gns[self._extract_uuid(f["id"])] = f["id"]
                    self._gns_to_name[f["id"]] = f.get("name", "")

                marker = sub.get("next_marker", "")
                if not marker:
                    break

            if len(all_dirs) >= max_dirs and len(all_files) >= max_files:
                logger.warning(f"Scan limits reached: {max_dirs} dirs, {max_files} files")
                break

        return all_dirs, all_files, skipped

    def _filter_new_files(self, all_files: list,
                          lib_gns: str) -> tuple[list, int]:
        """Compare with DB to find new/changed files. Returns (new_files, skipped_count)."""
        init_db()
        new_files = []
        skipped = 0
        with get_session() as s:
            for f in all_files:
                existing = s.exec(select(SyncDocumentMapping).where(
                    SyncDocumentMapping.source_doc_id == f["id"])).first()
                if existing and existing.source_rev == f.get("rev", ""):
                    skipped += 1  # unchanged, skip
                else:
                    new_files.append(f)
        return new_files, skipped

    def _create_folders(self, all_dirs: list, lib_gns: str,
                        ancestor_parent: int | None):
        """Create BISHENG folders matching AnyShare hierarchy."""
        self._folder_map = {}
        for d in sorted(all_dirs, key=lambda x: x["id"].count("/")):
            nm = d["name"]
            parent_gns = d["id"].rsplit("/", 1)[0] if "/" in d["id"] else ""

            if parent_gns == lib_gns:
                parent_id = ancestor_parent
            elif parent_gns in self._folder_map:
                parent_id = self._folder_map[parent_gns]
            else:
                parent_id = None

            try:
                fid = self._bs_folder.create(self._space_id, nm, parent_id=parent_id)
                self._folder_map[d["id"]] = fid
            except Exception as e:
                logger.warning(f"Folder create failed: {nm} — {e}")

    def _transfer_files(self, all_files: list, lib_gns: str,
                        ancestor_parent: int | None) -> tuple[int, int]:
        """Download from AnyShare → upload to BISHENG → register. Returns (ok, ng)."""
        td = Path.home() / "AppData" / "Local" / "Temp" / "as_sync" / uuid.uuid4().hex[:8]
        td.mkdir(parents=True, exist_ok=True)
        ok, ng = 0, 0
        self._file_map = {}

        # Shared client for connection reuse (avoids SSL flood)
        _shared_client = httpx.Client(timeout=120)
        for i, f in enumerate(all_files):
            nm, did = f["name"], f["id"]
            logger.debug(f"[{i+1}/{len(all_files)}] {nm[:60]}")
            try:
                # Throttle to avoid overwhelming AnyShare bucket
                import time as _t
                _t.sleep(0.3)

                # Download (retry on transient errors)
                for dretry in range(3):
                    try:
                        r = httpx.post(
                            f"{self._as_base}/api/efast/v1/file/osdownload",
                            json={"docid": did, "rev": "", "authtype": "QUERY_STRING",
                                  "savename": nm, "usehttps": True},
                            headers={"Authorization": f"Bearer {self._get_as_token()}"})
                        break
                    except (httpx.ConnectError, httpx.RemoteProtocolError,
                            httpx.ReadTimeout):
                        if dretry < 2:
                            _t.sleep(3 * (dretry + 1))
                        else:
                            raise
                a = r.json()["authrequest"]
                hh = {}
                for h in a[2:]:
                    if ": " in h:
                        k, v = h.split(": ", 1)
                        hh[k] = v

                sf = "".join(c for c in nm if c.isalnum() or c in "._-()（）")
                lp = td / sf
                for sretry in range(2):
                    try:
                        with _shared_client.stream(a[0], a[1], headers=hh) as rr:
                            rr.raise_for_status()
                            with open(lp, "wb") as ff:
                                for ch in rr.iter_bytes(65536):
                                    ff.write(ch)
                        break
                    except (httpx.ConnectError, httpx.RemoteProtocolError,
                            httpx.ReadTimeout):
                        if sretry < 1:
                            _t.sleep(3)
                        else:
                            raise

                # Upload
                fp = self._bs_file.upload_to_minio(self._space_id, lp)

                # Register (map parent folder)
                parent_gns = did.rsplit("/", 1)[0] if "/" in did else ""
                pfid = (ancestor_parent if parent_gns == lib_gns
                        else self._folder_map.get(parent_gns))
                reg = self._bs_file.register(self._space_id, fp, parent_id=pfid)
                fid = reg["id"]
                self._file_map[did] = fid

                ok += 1
                lp.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Transfer failed: {nm[:50]} — {e}")
                ng += 1

        shutil.rmtree(td, ignore_errors=True)
        return ok, ng

    def _write_mappings(self, lib_gns: str, space_name: str, source_type: str,
                        all_files: list, transferred: int,
                        trace_id: str, start_time) -> int | None:
        """Write sync state to mapping tables. Returns scan_run id."""
        init_db()
        now = datetime.datetime.now()
        scan_id = None

        with get_session() as s:
            # Scope config
            scope = s.exec(select(SyncScopeConfig).where(
                SyncScopeConfig.source_id == lib_gns)).first()
            if not scope:
                scope = SyncScopeConfig(
                    tenant_id=1, source_type=source_type,
                    source_id=lib_gns, source_name=space_name, enabled=True)
            s.add(scope)
            s.commit()

            # Scan run
            scan = SyncScanRun(
                tenant_id=1, scan_type="manual", scope_config_id=scope.id,
                total_files=len(all_files), new_files=len(all_files),
                status="completed", started_at=start_time,
                completed_at=now)
            s.add(scan)
            s.commit()
            scan_id = scan.id

            # Space mapping
            sm = s.exec(select(SyncSpaceMapping).where(
                SyncSpaceMapping.source_doc_lib_id == lib_gns)).first()
            if sm:
                sm.target_space_id = self._space_id
                sm.status = "created"
            else:
                sm = SyncSpaceMapping(
                    tenant_id=1, source_doc_lib_id=lib_gns,
                    source_doc_lib_name=space_name, source_type=source_type,
                    target_space_id=self._space_id, status="created")
                s.add(sm)
            s.commit()

            # Document mappings — insert new, update last_seen for existing
            for f in all_files:
                existing = s.exec(select(SyncDocumentMapping).where(
                    SyncDocumentMapping.source_doc_id == f["id"])).first()
                if existing:
                    existing.last_seen_scan_id = scan_id
                    existing.source_rev = f.get("rev", "")
                    existing.source_size = f.get("size", 0)
                    continue
                key = hashlib.sha256(
                    f"{lib_gns}|{f['id']}|{f.get('rev','')}".encode()
                ).hexdigest()[:32]
                s.add(SyncDocumentMapping(
                    tenant_id=1, space_mapping_id=sm.id,
                    source_doc_id=f["id"], source_rev=f.get("rev", ""),
                    source_name=f["name"], source_size=f.get("size", 0),
                    content_version=f.get("rev", ""), idempotency_key=key,
                    status="succeeded" if self._file_map.get(f["id"]) else "pending",
                    last_seen_scan_id=scan_id))

            # Audit
            s.add(SyncAuditEvent(
                tenant_id=1, trace_id=trace_id, action="sync",
                source_type=source_type, source_id=lib_gns,
                target_type="knowledge_space", target_id=self._space_id,
                operator="system", result="success",
                detail=f"Transferred {transferred}/{len(all_files)} files"))
            s.commit()

        return scan_id

    def _sync_permissions(self, all_dirs: list, all_files: list,
                          lib_gns: str, ancestor_parent: int | None,
                          no_root_perms: bool = False) -> tuple[int, int]:
        """Collect ACL → translate → batch authorize. Returns (synced_count, total_items)."""
        # Step 1: Collect ACL (skip root if no_root_perms)
        all_gns_list = [] if no_root_perms else [lib_gns]
        all_gns_list += [d["id"] for d in all_dirs]
        if getattr(self, '_file_map', None):  # only collect file ACLs if files were transferred
            all_gns_list += [f["id"] for f in all_files]
        acl_cache = {}
        dept_names = set()
        user_display_names = set()

        for gns in all_gns_list:
            try:
                r = httpx.post(
                    f"{self._as_base}/api/eacp/v1/perm2/get",
                    json={"docid": gns},
                    headers={"Authorization": f"Bearer {self._get_as_token()}"},
                    timeout=60)
                if r.status_code == 200:
                    perms = r.json().get("perminfos", [])
                    acl_cache[gns] = perms
                    for p in perms:
                        atype = p.get("accessortype", "user")
                        aname = p.get("accessorname", "")
                        if atype == "department":
                            dept_names.add(aname)
                        else:
                            uname, dname = parse_accessorname(aname)
                            if dname:
                                user_display_names.add(dname)
                            if uname:
                                user_display_names.add(uname)
            except Exception:
                pass

        logger.info(f"ACL cached: {len(acl_cache)} items, "
                     f"{len(user_display_names)} users, {len(dept_names)} depts")

        # Step 2: Resolve principals
        for name in user_display_names:
            try:
                self._mapper.resolve_principal(name, "user")
            except Exception:
                pass
        for name in dept_names:
            try:
                self._mapper.resolve_principal(name, "department")
            except Exception:
                pass
        logger.info(f"Principals resolved: {self._mapper.get_mapped_count()}")

        # Step 3: Build resource list and authorize
        def _translate_relation(allows: set) -> str | None:
            # Any ACL entry with download → viewer (no role escalation)
            if "download" not in allows:
                return None
            return "viewer"

        acl_items = []
        if lib_gns in acl_cache:
            acl_items.append((ancestor_parent or self._space_id, "folder",
                              "root", lib_gns))
        for d in all_dirs:
            fid = self._folder_map.get(d["id"])
            if fid:
                acl_items.append((fid, "folder", d["name"], d["id"]))
        for f in all_files:
            fid = getattr(self, '_file_map', {}).get(f["id"])
            if fid:
                acl_items.append((fid, "knowledge_file", f["name"], f["id"]))

        synced = 0
        for bs_id, res_type, name, gns in acl_items:
            perms = acl_cache.get(gns, [])
            if not perms:
                continue
            grants = []
            for p in perms:
                allows = set(p.get("allow", []))
                denys = set(p.get("deny", []))
                if denys:
                    continue
                rel = _translate_relation(allows)
                if rel is None:
                    continue

                atype = p.get("accessortype", "user")
                aname = p.get("accessorname", "")
                if atype == "department":
                    did = self._mapper.resolve_principal(aname, "department")
                    if did:
                        grants.append({"subject_type": "department",
                                       "subject_id": did, "relation": rel,
                                       "include_children": False})
                else:
                    uname, dname = parse_accessorname(aname)
                    uid = self._mapper.resolve_principal(dname or uname, "user")
                    if uid:
                        grants.append({"subject_type": "user",
                                       "subject_id": uid, "relation": rel})

            if not grants:
                continue

            ok = self._bs_perm.authorize(res_type, bs_id, grants=grants,
                                         timeout=60, retries=2)
            if ok:
                synced += 1
                logger.debug(f"  {res_type} {name[:35]}: {len(grants)} grants OK")
                # Write permission snapshot
                self._save_perm_snapshot(res_type, bs_id, name, gns, grants)
            else:
                logger.warning(f"  {res_type} {name[:35]}: FAIL")

        return synced, len(acl_items)

    def _save_perm_snapshot(self, res_type: str, bs_id: int,
                            name: str, gns: str, grants: list):
        """Write a SyncPermissionSnapshot record for audit trail."""
        try:
            from app.models.permission_snapshot import SyncPermissionSnapshot
            from app.models import get_session
            import json
            with get_session() as s:
                snap = SyncPermissionSnapshot(
                    tenant_id=1,
                    resource_type=res_type,
                    resource_id=f"{res_type}/{bs_id}",
                    source_acl_raw=json.dumps(
                        {"gns": gns, "name": name}, ensure_ascii=False),
                    target_fga_tuples=json.dumps(grants, ensure_ascii=False),
                    is_blocked=False,
                )
                s.add(snap)
                s.commit()
        except Exception as e:
            logger.debug(f"Snapshot write failed: {e}")
