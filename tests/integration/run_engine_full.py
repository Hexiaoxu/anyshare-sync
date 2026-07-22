"""Full engine run — ScanEngine + TransferCoordinator with real DB writes.

This is the production code path:
  ScanEngine → writes scan_run, document_mapping, task, audit_event
  TransferCoordinator → writes document_mapping updates, permission_snapshot, audit

Run: python tests/integration/run_engine_full.py
"""

import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, ".")

from app.config import AppConfig, AnyShareConfig, BishengConfig, SyncConfig, SchedulerConfig
from app.models import init_db, get_session
from app.models.scope_config import SyncScopeConfig
from app.services.scan_engine import ScanEngine
from app.services.transfer_coordinator import TransferCoordinator
from app.services.principal_mapper import PrincipalMapper
from sqlmodel import select
from app.models.task import SyncTask
from app.models.scan_run import SyncScanRun
from app.models.document_mapping import SyncDocumentMapping
from app.models.space_mapping import SyncSpaceMapping
from app.models.folder_mapping import SyncFolderMapping
from app.models.permission_snapshot import SyncPermissionSnapshot
from app.models.audit_event import SyncAuditEvent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("engine-full")

# ── Config ────────────────────────────────────────────────
config = AppConfig(
    anyshare=AnyShareConfig(
        base_url="https://5j-zsgl.powerchina.cn",
        client_id="test",  # not used with cookie token
        client_secret="test",
    ),
    bisheng=BishengConfig(
        base_url="http://192.168.106.161:7860",
        cookie_value="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ7XCJ1c2VyX2lkXCI6IDEsIFwidXNlcl9uYW1lXCI6IFwiYWRtaW5cIiwgXCJ0ZW5hbnRfaWRcIjogMSwgXCJ0b2tlbl92ZXJzaW9uXCI6IDF9IiwiZXhwIjoxNzg0MTAxMDk4LCJpc3MiOiJiaXNoZW5nIn0.U3OsX2VeLKjKKoFyA4UkBQ91sy1VSU6zjE_mkrjpKpg",
    ),
    sync=SyncConfig(max_depth=3, max_objects=100, temp_dir=str(Path.home() / "AppData" / "Local" / "Temp" / "anyshare-sync")),
    scheduler=SchedulerConfig(),
)

# ── Main ───────────────────────────────────────────────────
def main():
    init_db()
    trace_id = uuid.uuid4().hex[:12]
    logger.info(f"=== Engine Full Run — trace_id={trace_id} ===")

    # 1. Create scope config for test user's personal doc lib
    with get_session() as session:
        existing = session.exec(
            select(SyncScopeConfig).where(SyncScopeConfig.source_id == "gns://110F8E071F0243AEBDB4DFD59F52D131")
        ).first()
        if existing:
            scope_id = existing.id
            logger.info(f"Using existing scope id={scope_id}")
        else:
            scope = SyncScopeConfig(
                tenant_id=1,
                source_type="user_doc_lib",
                source_id="gns://110F8E071F0243AEBDB4DFD59F52D131",
                source_name="5jliming1",
                enabled=True,
            )
            session.add(scope)
            session.commit()
            scope_id = scope.id
            logger.info(f"Created scope id={scope_id}")

    # 2. Run ScanEngine with browser token
    AS_TOKEN = "ory_at_RM7bAaE8zEeGAlbzehHMZDcvVSJLJhnVvjTXqd929U8.Y0UaOt-Nj3P-o1s69oNFuHPxlgLR18plRRE83ZhT-eM"
    logger.info("=== Step 1: ScanEngine.run_full_scan() ===")
    engine = ScanEngine(config, token=AS_TOKEN)
    results = engine.run_full_scan(tenant_id=1)
    logger.info(f"Scan results: {results}")

    # 3.5. Resolve space: create BISHENG space + space_mapping
    logger.info("=== Step 1.5: Resolve space mapping ===")
    with get_session() as session:
        sm = session.exec(
            select(SyncSpaceMapping).where(SyncSpaceMapping.source_doc_lib_id == "gns://110F8E071F0243AEBDB4DFD59F52D131")
        ).first()
        if not sm:
            sm = SyncSpaceMapping(
                tenant_id=1,
                source_doc_lib_id="gns://110F8E071F0243AEBDB4DFD59F52D131",
                source_doc_lib_name="5jliming1",
                source_type="personal",
                status="pending",
            )
            session.add(sm)
            session.commit()
            logger.info(f"Created space_mapping id={sm.id}")

        if not sm.target_space_id:
            # Create space in BISHENG
            import httpx
            r = httpx.Client(timeout=30).post(
                "http://192.168.106.161:7860/api/v1/knowledge/space",
                json={"name": "5jliming1_personal", "description": "Engine test", "auth_type": "public"},
                cookies={"access_token_cookie": config.bisheng.cookie_value},
            )
            r.raise_for_status()
            space_id = r.json()["data"]["id"]
            sm.target_space_id = space_id
            sm.status = "created"
            session.add(sm)
            session.commit()
            logger.info(f"Created BISHENG space id={space_id}, updated space_mapping")

        # Also update document_mappings with the space_mapping_id
        docs = session.exec(
            select(SyncDocumentMapping).where(SyncDocumentMapping.space_mapping_id == scope_id)
        ).all()
        for d in docs:
            d.space_mapping_id = sm.id
        # Update tasks too
        tasks = session.exec(
            select(SyncTask).where(SyncTask.scan_run_id == 1)
        ).all()
        for t in tasks:
            t.target_space_id = space_id
        session.commit()
        logger.info(f"Updated {len(docs)} doc_mappings + {len(tasks)} tasks with space_mapping_id={sm.id} space_id={space_id}")
    with get_session() as session:
        scans = session.exec(select(SyncScanRun).order_by(SyncScanRun.id.desc()).limit(3)).all()
        for s in scans:
            logger.info(f"  scan_run id={s.id} status={s.status} folders={s.total_folders} files={s.total_files} new={s.new_files}")

        docs = session.exec(select(SyncDocumentMapping).limit(10)).all()
        for d in docs:
            logger.info(f"  doc_map id={d.id} source={d.source_name} status={d.status} rev={d.source_rev[:20]}")

        tasks = session.exec(select(SyncTask).order_by(SyncTask.id.desc()).limit(10)).all()
        for t in tasks:
            logger.info(f"  task id={t.id} action={t.action} status={t.status} source={t.source_doc_id[:50]}")

    # 4. Run transfers for pending tasks
    logger.info("=== Step 2: TransferCoordinator ===")
    with get_session() as session:
        pending = session.exec(
            select(SyncTask).where(SyncTask.status == "pending")
        ).all()
        logger.info(f"Pending tasks: {len(pending)}")

    mapper = PrincipalMapper()
    coordinator = TransferCoordinator(config, mapper, token=AS_TOKEN)

    for task in pending[:3]:  # limit to 3
        logger.info(f"Transferring task {task.id}: {task.action} {task.source_doc_id[:60]}")
        result = coordinator.transfer_one(task)
        logger.info(f"  Result: {result}")

    # 5. Final database state
    logger.info("=== Final DB State ===")
    with get_session() as session:
        for table_name, model in [
            ("scope_config", SyncScopeConfig),
            ("scan_run", SyncScanRun),
            ("document_mapping", SyncDocumentMapping),
            ("folder_mapping", SyncFolderMapping),
            ("space_mapping", SyncSpaceMapping),
            ("task", SyncTask),
            ("permission_snapshot", SyncPermissionSnapshot),
            ("audit_event", SyncAuditEvent),
        ]:
            from sqlmodel import func
            count = session.exec(select(func.count()).select_from(model)).one()
            logger.info(f"  {table_name}: {count} rows")

    logger.info(f"=== Engine Full Run COMPLETE — trace_id={trace_id} ===")
    logger.info("Space created: check BISHENG frontend for '5jliming1_personal' space")


if __name__ == "__main__":
    main()
