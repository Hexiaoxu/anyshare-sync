"""Admin API — health, trigger scan, query tasks."""

from fastapi import APIRouter, Query

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/health")
def health():
    return {"status": "OK", "service": "anyshare-sync"}


@router.get("/scopes")
def list_scopes(tenant_id: int = Query(default=1)):
    """List configured sync scopes."""
    from app.models import get_session, SyncScopeConfig
    from sqlmodel import select

    with get_session() as session:
        scopes = session.exec(
            select(SyncScopeConfig).where(SyncScopeConfig.tenant_id == tenant_id)
        ).all()
        return {"data": [s.model_dump() for s in scopes]}


@router.get("/tasks")
def list_tasks(status: str = Query(default=None), limit: int = Query(default=50)):
    """List recent tasks, optionally filtered by status."""
    from app.models import get_session, SyncTask
    from sqlmodel import select

    with get_session() as session:
        stmt = select(SyncTask).order_by(SyncTask.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(SyncTask.status == status)
        tasks = session.exec(stmt).all()
        return {"data": [t.model_dump() for t in tasks]}


@router.get("/tasks/{task_id}")
def get_task(task_id: int):
    """Get a specific task by ID."""
    from app.models import get_session, SyncTask

    with get_session() as session:
        task = session.get(SyncTask, task_id)
        if task is None:
            return {"error": "not found"}
        return {"data": task.model_dump()}


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: int):
    """Manually retry a failed task."""
    from app.models import get_session, SyncTask
    from datetime import datetime

    with get_session() as session:
        task = session.get(SyncTask, task_id)
        if task is None:
            return {"error": "not found"}
        task.status = "pending"
        task.retry_count = 0
        task.next_retry_at = datetime.now()
        task.error_message = None
        session.add(task)
        session.commit()
        return {"data": task.model_dump()}


@router.get("/audit")
def list_audit(limit: int = Query(default=50)):
    """List recent audit events."""
    from app.models import get_session, SyncAuditEvent
    from sqlmodel import select

    with get_session() as session:
        events = session.exec(
            select(SyncAuditEvent).order_by(SyncAuditEvent.created_at.desc()).limit(limit)
        ).all()
        return {"data": [e.model_dump() for e in events]}
