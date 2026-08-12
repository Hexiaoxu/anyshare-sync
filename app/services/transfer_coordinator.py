"""Transfer coordinator — download → validate → upload → register → track.

Orchestrates the complete file transfer from AnyShare to BISHENG.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.connectors.anyshare.auth import AnyShareAuth
from app.connectors.anyshare.downloader import AnyShareDownloader
from app.connectors.anyshare.acl import AnyShareAcl
from app.connectors.bisheng.client import BishengClient
from app.connectors.bisheng.space import BishengSpace
from app.connectors.bisheng.folder import BishengFolder
from app.connectors.bisheng.file_transfer import BishengFileTransfer
from app.connectors.bisheng.permission import BishengPermission
from app.services.permission_gate import PermissionGate, GateDecision
from app.services.permission_translator import PermissionTranslator
from app.services.principal_mapper import PrincipalMapper
from app.services.ingestion_tracker import IngestionTracker
from app.models import get_session
from app.models.document_mapping import SyncDocumentMapping
from app.models.space_mapping import SyncSpaceMapping
from app.models.folder_mapping import SyncFolderMapping
from app.models.permission_snapshot import SyncPermissionSnapshot
from app.models.task import SyncTask
from app.models.audit_event import SyncAuditEvent
from sqlmodel import select

if TYPE_CHECKING:
    from app.config import AppConfig

logger = logging.getLogger(__name__)


class TransferCoordinator:
    """Handles one file transfer end-to-end."""

    def __init__(self, config: "AppConfig", principal_mapper: PrincipalMapper, token: str = None):
        self._config = config
        self._mapper = principal_mapper

        # AnyShare clients
        _get_token = (lambda *a: token) if token else None
        _get_user = (lambda *a: token) if token else None
        if token:
            self._as_auth = None
        else:
            self._as_auth = AnyShareAuth(
                base_url=config.anyshare.base_url,
                client_id=config.anyshare.client_id,
                client_secret=config.anyshare.client_secret,
            )
            _get_token = self._as_auth.get_app_token
            _get_user = self._as_auth.get_user_token

        admin_account = getattr(config.anyshare, "admin_account", None)
        _get_acl_token = (lambda *a: token) if token else (
            (lambda: self._as_auth.get_user_token(admin_account)) if admin_account else _get_token
        )
        self._downloader = AnyShareDownloader(
            base_url=config.anyshare.base_url,
            get_user_token=_get_user,
        )
        self._acl = AnyShareAcl(
            base_url=config.anyshare.base_url,
            get_token=_get_acl_token,
        )

        # BISHENG clients
        self._bs_client = BishengClient(
            base_url=config.bisheng.base_url,
            cookie_value=config.bisheng.cookie_value,
        )
        self._bs_space = BishengSpace(self._bs_client)
        self._bs_folder = BishengFolder(self._bs_client)
        self._bs_file = BishengFileTransfer(self._bs_client)
        self._bs_perm = BishengPermission(self._bs_client)

        self._gate = PermissionGate()
        self._translator = PermissionTranslator(self._mapper)
        self._tracker = IngestionTracker(self._make_status_func())

    def transfer_one(self, task: SyncTask) -> dict:
        """Execute a single transfer task. Returns result dict."""
        trace_id = uuid.uuid4().hex[:12]
        task_dir = Path(self._config.sync.temp_dir) / trace_id
        local_file: Path | None = None

        try:
            # 1. Mark running
            with get_session() as s:
                t = s.get(SyncTask, task.id)
                if t: t.status = "running"; s.add(t); s.commit()

            # 2. Resolve space mapping
            with get_session() as s:
                mapping = s.exec(
                    select(SyncDocumentMapping).where(
                        SyncDocumentMapping.source_doc_id == task.source_doc_id,
                    )
                ).first()
                if not mapping:
                    return {"status": "failed", "reason": "no_mapping"}
                space_map = s.get(SyncSpaceMapping, mapping.space_mapping_id)
                if not space_map or not space_map.target_space_id:
                    return {"status": "failed", "reason": "no_target_space"}

            space_id = space_map.target_space_id

            # 3. Resolve target folder
            parent_id = None
            if mapping.folder_mapping_id:
                with get_session() as s:
                    fm = s.get(SyncFolderMapping, mapping.folder_mapping_id)
                    if fm and fm.target_folder_id:
                        parent_id = fm.target_folder_id

            # 4. Download from AnyShare
            # Get file name from mapping
            doc_name = ""
            if mapping:
                doc_name = mapping.source_name or ""
            download_info = self._downloader.get_download_info(task.source_doc_id, name=doc_name)
            local_file = self._downloader.download_to_file(download_info, task_dir)

            # 5. Upload + Register with BISHENG
            file_path = self._bs_file.upload_to_minio(space_id, local_file)
            file_record = self._bs_file.register(space_id, file_path, parent_id)

            target_file_id = file_record["id"]

            # 6. Update mapping
            with get_session() as s:
                dm = s.exec(
                    select(SyncDocumentMapping).where(
                        SyncDocumentMapping.source_doc_id == task.source_doc_id,
                    )
                ).first()
                if dm:
                    dm.target_file_id = target_file_id
                    dm.target_upload_ref = file_path
                    dm.status = "bisheng_registered"
                    s.add(dm)
                    s.commit()

            # 7. Wait for ingestion
            result = self._tracker.wait(target_file_id)
            final_status = "succeeded" if result.status == 2 else "failed"

            # 8. Get version info
            versions = self._bs_file.get_versions(target_file_id)
            vid = versions["versions"][0]["version_id"] if versions.get("versions") else None
            did = versions.get("document_id")

            # 9. Update document mapping with version data
            with get_session() as s:
                dm = s.exec(
                    select(SyncDocumentMapping).where(
                        SyncDocumentMapping.source_doc_id == task.source_doc_id,
                    )
                ).first()
                if dm:
                    dm.target_document_id = did
                    dm.target_version_id = vid
                    dm.status = final_status
                    s.add(dm)
                    s.commit()

            # 10. Audit
            with get_session() as s:
                s.add(SyncAuditEvent(
                    tenant_id=task.tenant_id, trace_id=trace_id,
                    action="transfer", source_type="file",
                    source_id=task.source_doc_id, source_rev=task.source_rev,
                    target_type="knowledge_file", target_id=target_file_id,
                    operator="system", result=final_status,
                ))
                t = s.get(SyncTask, task.id)
                if t: t.status = "completed"; s.add(t); s.commit()

            return {"status": final_status, "file_id": target_file_id,
                    "document_id": did, "version_id": vid}

        except Exception as e:
            logger.exception(f"Transfer failed for {task.source_doc_id}")
            with get_session() as s:
                t = s.get(SyncTask, task.id)
                if t:
                    t.status = "failed"
                    t.error_message = str(e)[:4000]
                    t.retry_count = (t.retry_count or 0) + 1
                    if t.retry_count >= t.max_retries:
                        t.status = "dead_letter"
                    s.add(t)
                s.add(SyncAuditEvent(
                    tenant_id=task.tenant_id, trace_id=trace_id,
                    action="transfer", source_id=task.source_doc_id,
                    result="failed", detail=str(e)[:8000],
                ))
                s.commit()
            return {"status": "failed", "reason": str(e)[:200]}

        finally:
            if local_file and local_file.exists():
                local_file.unlink(missing_ok=True)
            if task_dir.exists():
                shutil.rmtree(task_dir, ignore_errors=True)

    def sync_permissions(self, space_mapping_id: int) -> dict:
        """Sync permissions for a space: fetch ACL → translate → batch write FGA tuples."""
        with get_session() as s:
            sm = s.get(SyncSpaceMapping, space_mapping_id)
            if not sm or not sm.target_space_id:
                return {"status": "skipped", "reason": "no_space"}

            space_id = sm.target_space_id

            # Set API context for on-demand principal resolution
            self._mapper.set_api_context(self._bs_perm, space_id)

            # Get ACL from AnyShare
            entries = self._acl.get_acl(sm.source_doc_lib_id)

            # Gate check
            gate_result = self._gate.check_document(entries)
            if gate_result.decision == GateDecision.BLOCK:
                snap = SyncPermissionSnapshot(
                    space_mapping_id=space_mapping_id,
                    resource_type="space",
                    resource_id=str(space_id),
                    source_acl_raw=self._acl.serialize_acl(entries),
                    is_blocked=True,
                    block_reason=gate_result.reason,
                )
                s.add(snap)
                s.commit()
                return {"status": "blocked", "reason": gate_result.reason}

            # Translate
            result = self._translator.translate(
                entries, "knowledge_space", space_id,
            )

            # Batch write FGA tuples — group by resource (object_type + object_id)
            grants_by_resource: dict[tuple[str, int], list[dict]] = {}
            for tup in result.tuples:
                key = (tup.object_type, tup.object_id)
                grant = {"subject_type": tup.subject_type,
                         "subject_id": tup.subject_id,
                         "relation": tup.relation}
                if grant["subject_type"] == "department":
                    grant["include_children"] = True
                grants_by_resource.setdefault(key, []).append(grant)

            synced = 0
            for (res_type, res_id), grants in grants_by_resource.items():
                ok = self._bs_perm.authorize(res_type, res_id, grants=grants, timeout=60, retries=2)
                if ok:
                    synced += len(grants)
                else:
                    logger.warning(f"Batch grant failed for {res_type}/{res_id}: {len(grants)} grants")

            # Snapshot
            snap = SyncPermissionSnapshot(
                space_mapping_id=space_mapping_id,
                resource_type="space",
                resource_id=str(space_id),
                source_acl_raw=self._acl.serialize_acl(entries),
                target_fga_tuples=str([vars(t) for t in result.tuples]),
                is_blocked=len(result.blocked) > 0,
                block_reason="; ".join(b["reason"] for b in result.blocked),
                policy_hash=str(hash(self._acl.serialize_acl(entries))),
            )
            s.add(snap)
            s.commit()

        return {"status": "synced", "tuples": synced,
                "blocked": len(result.blocked)}

    def _make_status_func(self):
        """Return a closure for polling BISHENG file status."""
        bs_file = self._bs_file
        def get_status(file_id: int) -> int:
            with get_session() as s:
                dm = s.exec(
                    select(SyncDocumentMapping).where(
                        SyncDocumentMapping.target_file_id == file_id,
                    )
                ).first()
                if dm and dm.space_mapping_id:
                    sm = s.get(SyncSpaceMapping, dm.space_mapping_id)
                    if sm and sm.target_space_id:
                        return bs_file.get_status(file_id, sm.target_space_id)
            return -1
        return get_status
