"""Scan run tracking — one row per scan execution."""

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class SyncScanRun(SQLModel, table=True):
    __tablename__ = "anyshare_sync_scan_run"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(default=1, index=True)
    scan_type: str = Field(max_length=32)
    scope_config_id: Optional[int] = Field(default=None, index=True)
    status: str = Field(default="running", max_length=32)
    total_folders: int = Field(default=0)
    total_files: int = Field(default=0)
    new_files: int = Field(default=0)
    updated_files: int = Field(default=0)
    deleted_files: int = Field(default=0)
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = Field(default=None)
    error_message: Optional[str] = Field(default=None, max_length=4096)
