"""Sync task table — one row per transfer/permission action."""

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class SyncTask(SQLModel, table=True):
    __tablename__ = "anyshare_sync_task"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(default=1, index=True)
    scan_run_id: Optional[int] = Field(default=None, index=True)
    idempotency_key: str = Field(max_length=256, unique=True, index=True)
    action: str = Field(max_length=32)
    source_doc_id: str = Field(max_length=1024, index=True)
    source_rev: str = Field(default="", max_length=256)
    target_space_id: Optional[int] = Field(default=None, index=True)
    target_file_id: Optional[int] = Field(default=None)
    status: str = Field(default="pending", max_length=32)
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=6)
    next_retry_at: Optional[datetime] = Field(default=None)
    lease_holder: str = Field(default="", max_length=128)
    lease_expires_at: Optional[datetime] = Field(default=None)
    error_message: Optional[str] = Field(default=None, max_length=4096)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
