"""Celery async tasks — wired to real engine.

Usage:
  celery -A app.workers.tasks worker -l info -P threads -c 4
  celery -A app.workers.tasks beat -l info
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from celery import Celery
from celery.schedules import crontab
from sqlmodel import select

from app.config import load_config
from app.models.base import init_db, get_session, engine
from app.models.task import SyncTask
from app.models.audit_event import SyncAuditEvent
from app.models.document_mapping import SyncDocumentMapping
from app.models.space_mapping import SyncSpaceMapping

logger = logging.getLogger("anyshare-sync.worker")

config = load_config()

app = Celery("anyshare-sync")
app.conf.update(
    broker_url=config.anyshare.broker_url if hasattr(config.anyshare, 'broker_url') else "redis://localhost:6379/3",
    result_backend="redis://localhost:6379/3",
    timezone="Asia/Shanghai",
    task_serializer="json",
    accept_content=["json"],
    beat_schedule={
        "retry-due-tasks": {
            "task": "app.workers.tasks.retry_due_tasks",
            "schedule": config.scheduler.retry_due_seconds,
        },
        "daily-incremental-scan": {
            "task": "app.workers.tasks.daily_incremental_scan",
            "schedule": crontab(
                hour=int(config.scheduler.daily_scan_time.split(":")[0]),
                minute=int(config.scheduler.daily_scan_time.split(":")[1]),
            ),
        },
        "daily-housekeeping": {
            "task": "app.workers.tasks.daily_housekeeping",
            "schedule": crontab(
                hour=int(config.scheduler.daily_housekeeping_time.split(":")[0]),
                minute=int(config.scheduler.daily_housekeeping_time.split(":")[1]),
            ),
        },
    },
)


@app.task(bind=True, max_retries=3)
def run_scan(self, tenant_id: int = 1):
    """Run a full incremental scan for all enabled scopes."""
    from app.services.scan_engine import ScanEngine

    logger.info(f"Starting scan cycle (tenant={tenant_id})")
    try:
        engine = ScanEngine(config)
        results = engine.run_full_scan(tenant_id)
        logger.info(f"Scan complete: {results}")
        return results
    except Exception as e:
        logger.exception(f"Scan failed: {e}")
        raise self.retry(exc=e, countdown=300)


@app.task(bind=True, max_retries=3)
def run_transfer(self, task_id: int):
    """Execute a single transfer task."""
    from app.services.transfer_coordinator import TransferCoordinator
    from app.services.principal_mapper import PrincipalMapper

    with get_session() as session:
        task = session.get(SyncTask, task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}
        if task.status == "completed":
            return {"status": "already_completed"}

    logger.info(f"Starting transfer task {task_id}: {task.action} {task.source_doc_id}")
    try:
        mapper = PrincipalMapper()
        coordinator = TransferCoordinator(config, mapper)
        result = coordinator.transfer_one(task)
        logger.info(f"Transfer {task_id} result: {result}")
        return result
    except Exception as e:
        logger.exception(f"Transfer {task_id} failed: {e}")
        raise self.retry(exc=e, countdown=60)


@app.task
def retry_due_tasks():
    """Pull tasks past next_retry_at and re-enqueue as transfer tasks."""
    with get_session() as session:
        due = session.exec(
            select(SyncTask).where(
                SyncTask.status.in_(["failed", "retry_wait"]),
                SyncTask.retry_count < SyncTask.max_retries,
                SyncTask.next_retry_at <= datetime.now(),
            )
        ).all()

        count = 0
        for task in due:
            task.status = "retry_wait"
            task.next_retry_at = datetime.now() + timedelta(
                seconds=config.sync.retry_backoff_seconds * (2 ** (task.retry_count or 1))
            )
            session.add(task)
            count += 1
            # Actually retry
            run_transfer.delay(task.id)

        session.commit()
        if count:
            logger.info(f"Re-enqueued {count} due tasks")


@app.task
def daily_incremental_scan():
    """Daily: scan all enabled scopes, generate transfer tasks, enqueue them."""
    from app.services.scan_engine import ScanEngine

    logger.info("=== Daily incremental scan starting ===")
    engine = ScanEngine(config)
    results = engine.run_full_scan()

    # Enqueue all pending transfer tasks
    with get_session() as session:
        pending = session.exec(
            select(SyncTask).where(SyncTask.status == "pending")
        ).all()
        for task in pending:
            run_transfer.delay(task.id)
        logger.info(f"Enqueued {len(pending)} transfer tasks")

    logger.info(f"=== Daily scan complete: {results} ===")
    return results


@app.task
def daily_housekeeping():
    """Clean orphan temp files, recycle stale leases, update missing counts."""
    import shutil

    # Clean temp dir
    temp_dir = Path(config.sync.temp_dir)
    if temp_dir.exists():
        removed = 0
        for item in temp_dir.iterdir():
            if item.is_dir():
                try:
                    shutil.rmtree(item)
                    removed += 1
                except Exception:
                    pass
        logger.info(f"Housekeeping: cleaned {removed} orphan temp dirs")

    # Recycle stale leases
    with get_session() as session:
        stale = session.exec(
            select(SyncTask).where(
                SyncTask.status == "running",
                SyncTask.lease_expires_at < datetime.now(),
            )
        ).all()
        for t in stale:
            t.status = "failed"
            t.lease_holder = ""
            t.error_message = "Lease expired — recycled"
            session.add(t)
        session.commit()
        if stale:
            logger.info(f"Housekeeping: recycled {len(stale)} stale leases")


@app.task
def poll_ingestion_status():
    """Poll ingestion status for in-flight files, update mappings."""
    from app.connectors.bisheng.client import BishengClient

    client = BishengClient(config.bisheng.base_url, config.bisheng.cookie_value)

    with get_session() as session:
        inflight = session.exec(
            select(SyncDocumentMapping).where(
                SyncDocumentMapping.status == "bisheng_registered",
            )
        ).all()

        updated = 0
        for dm in inflight:
            if not dm.target_file_id:
                continue
            sm = session.get(SyncSpaceMapping, dm.space_mapping_id)
            if not sm or not sm.target_space_id:
                continue

            try:
                resp = client._get(
                    f"/api/v1/knowledge/space/{sm.target_space_id}/children",
                    {"page": 1, "page_size": 200},
                )
                data = client.ok(resp)
                items = data.get("data", data.get("data", []))
                for item in items:
                    if item["id"] == dm.target_file_id:
                        if item["status"] == 2:  # SUCCESS
                            dm.status = "succeeded"
                            updated += 1
                        elif item["status"] in (3, 7):  # FAILED, VIOLATION
                            dm.status = "failed"
                            updated += 1
                        break
            except Exception as e:
                logger.warning(f"Poll failed for file {dm.target_file_id}: {e}")

        if updated:
            session.commit()
            logger.info(f"Poll: updated {updated} document statuses")
