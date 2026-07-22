"""Sync scope configuration."""

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class SyncScopeConfig(SQLModel, table=True):
    __tablename__ = "anyshare_sync_scope_config"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(default=1, index=True)
    source_type: str = Field(max_length=32)
    source_id: str = Field(max_length=1024)
    source_name: str = Field(max_length=256, default="")
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
