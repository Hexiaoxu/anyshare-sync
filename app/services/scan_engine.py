"""Scan engine — the main loop.

Orchestrates one complete sync cycle:
  1. Load enabled scopes
  2. Per scope: scan AnyShare tree → compute diffs → generate tasks
  3. Emit audit events
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from app.connectors.anyshare.auth import AnyShareAuth
from app.connectors.anyshare.scanner import AnyShareScanner
from app.connectors.anyshare.doclib import AnyShareDocLib, DocLibType
from app.models import get_session
from app.models.scan_run import SyncScanRun
from app.models.scope_config import SyncScopeConfig
from app.models.document_mapping import SyncDocumentMapping
from app.models.task import SyncTask
from app.models.audit_event import SyncAuditEvent
from app.services.diff_calculator import DiffCalculator, DiffAction
from sqlmodel import select

if TYPE_CHECKING:
    from app.config import AppConfig

logger = logging.getLogger(__name__)


class ScanEngine:
    """Runs a full scan-and-diff cycle."""

    def __init__(self, config: "AppConfig", token: str = None):
        self._config = config
        if token:
            self._auth = None  # using direct token
        else:
            self._auth = AnyShareAuth(
                base_url=config.anyshare.base_url,
                client_id=config.anyshare.client_id,
                client_secret=config.anyshare.client_secret,
            )
        self._token = token
        _get_token = (lambda *a: token) if token else self._auth.get_app_token
        _get_user = (lambda *a: token) if token else self._auth.get_user_token
        admin_account = config.anyshare.admin_account if hasattr(config.anyshare, "admin_account") else None
        _get_scan_token = (lambda *a: token) if token else (
            (lambda: self._auth.get_user_token(admin_account)) if admin_account else self._auth.get_app_token
        )
        self._doclib = AnyShareDocLib(
            base_url=config.anyshare.base_url,
            get_app_token=_get_token,
            get_user_token=_get_user,
            admin_account=admin_account,
        )
        self._scanner = AnyShareScanner(
            base_url=config.anyshare.base_url,
            get_token=_get_scan_token,
        )
        self._diff = DiffCalculator()

    def run_full_scan(self, tenant_id: int = 1) -> dict:
        """Run a complete scan cycle for all enabled scopes."""
        trace_id = uuid.uuid4().hex[:12]

        with get_session() as session:
            scopes = session.exec(
                select(SyncScopeConfig).where(
                    SyncScopeConfig.tenant_id == tenant_id,
                    SyncScopeConfig.enabled == True,
                )
            ).all()

            results = {"scopes": len(scopes), "scans": [], "total_diffs": 0}

            for scope in scopes:
                scan = SyncScanRun(
                    tenant_id=tenant_id,
                    scan_type="incremental",
                    scope_config_id=scope.id,
                )
                session.add(scan)
                session.commit()
                scan_id = scan.id

                logger.info(f"Scanning scope: {scope.source_name} ({scope.source_type})")

                try:
                    # Step 1: Scan AnyShare
                    scan_result = self._scanner.scan(scope.source_id)

                    # Step 2: Load existing mappings
                    existing = session.exec(
                        select(SyncDocumentMapping).where(
                            SyncDocumentMapping.space_mapping_id == scope.id,
                        )
                    ).all()
                    existing_map = {m.source_doc_id: m for m in existing}

                    # Step 3: Compute diffs
                    diffs = self._diff.compute_diff(
                        scan_result.files, existing_map,
                        missing_threshold=self._config.sync.missing_threshold,
                    )

                    # Step 4: Generate tasks for diffs
                    task_count = 0
                    for d in diffs:
                        if d.action == DiffAction.NO_CHANGE:
                            continue
                        idem_key = self._make_idempotency_key(
                            tenant_id, scope.source_type, d.source_doc_id,
                            d.source_rev, d.action.value,
                        )
                        task = SyncTask(
                            tenant_id=tenant_id,
                            scan_run_id=scan_id,
                            idempotency_key=idem_key,
                            action=d.action.value,
                            source_doc_id=d.source_doc_id,
                            source_rev=d.source_rev,
                            status="pending",
                        )
                        session.add(task)
                        task_count += 1

                        # Update or create document mapping
                        if d.action != DiffAction.DELETE_CANDIDATE:
                            mapping = existing_map.get(d.source_doc_id)
                            if mapping is None:
                                mapping = SyncDocumentMapping(
                                    space_mapping_id=scope.id,
                                    source_doc_id=d.source_doc_id,
                                    source_name=d.source_name,
                                    source_size=d.source_size,
                                )
                            mapping.source_rev = d.source_rev
                            mapping.source_name = d.source_name
                            mapping.content_version = d.content_version_new
                            mapping.metadata_hash = d.metadata_hash_new
                            mapping.last_seen_scan_id = scan_id
                            mapping.missing_count = 0
                            mapping.status = "transfer_pending"
                            session.add(mapping)
                        else:
                            # Increment missing count
                            if d.source_doc_id in existing_map:
                                m = existing_map[d.source_doc_id]
                                m.missing_count = (m.missing_count or 0) + 1
                                session.add(m)

                    session.commit()

                    # Update scan record
                    scan.total_folders = scan_result.total_folders
                    scan.total_files = scan_result.total_files
                    scan.new_files = sum(1 for d in diffs if d.action == DiffAction.CREATE)
                    scan.updated_files = sum(1 for d in diffs if d.action == DiffAction.UPDATE)
                    scan.completed_at = datetime.now()
                    scan.status = "completed"
                    session.add(scan)

                    # Audit
                    session.add(SyncAuditEvent(
                        tenant_id=tenant_id,
                        trace_id=trace_id,
                        action="scan",
                        source_type=scope.source_type,
                        source_id=scope.source_id,
                        result="success",
                        detail=str({"diffs": len(diffs), "tasks": task_count}),
                    ))
                    session.commit()

                    results["scans"].append({
                        "scope": scope.source_name,
                        "diffs": len(diffs),
                        "tasks": task_count,
                        "folders": scan_result.total_folders,
                        "files": scan_result.total_files,
                    })
                    results["total_diffs"] += len(diffs)

                except Exception as e:
                    logger.exception(f"Scan failed for {scope.source_name}")
                    scan.status = "failed"
                    scan.error_message = str(e)[:4000]
                    scan.completed_at = datetime.now()
                    session.add(scan)
                    session.add(SyncAuditEvent(
                        tenant_id=tenant_id, trace_id=trace_id,
                        action="scan", result="failed",
                        detail=str(e)[:8000],
                    ))
                    session.commit()

        return results

    @staticmethod
    def _make_idempotency_key(tenant_id: int, resource_type: str,
                               source_id: str, rev: str, action: str) -> str:
        import hashlib
        raw = f"{tenant_id}|{resource_type}|{source_id}|{rev}|{action}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]
