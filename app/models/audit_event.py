"""Audit event log — append-only, never delete."""

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class SyncAuditEvent(SQLModel, table=True):
    __tablename__ = "anyshare_sync_audit_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(default=1, index=True)
    trace_id: str = Field(max_length=128, index=True)
    action: str = Field(max_length=64)
    source_type: Optional[str] = Field(default=None, max_length=32)
    source_id: Optional[str] = Field(default=None, max_length=1024)
    source_rev: Optional[str] = Field(default=None, max_length=256)
    target_type: Optional[str] = Field(default=None, max_length=32)
    target_id: Optional[int] = Field(default=None)
    operator: str = Field(default="system", max_length=128)
    result: str = Field(max_length=32)
    detail: str = Field(default="{}", max_length=8192)
    policy_hash: Optional[str] = Field(default=None, max_length=256)
    created_at: datetime = Field(default_factory=datetime.now)
