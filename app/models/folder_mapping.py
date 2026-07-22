"""Folder mapping table — tracks AnyShare dir → BISHENG folder."""

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class SyncFolderMapping(SQLModel, table=True):
    __tablename__ = "anyshare_sync_folder_mapping"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(default=1, index=True)
    space_mapping_id: int = Field(index=True)
    source_folder_id: str = Field(max_length=1024, index=True)
    source_parent_id: Optional[str] = Field(default=None, max_length=1024)
    source_name: str = Field(max_length=256)
    source_rev: str = Field(default="", max_length=256)
    source_path: str = Field(default="", max_length=4096)
    target_space_id: Optional[int] = Field(default=None)
    target_folder_id: Optional[int] = Field(default=None, index=True)
    target_parent_id: Optional[int] = Field(default=None)
    metadata_hash: str = Field(default="", max_length=256)
    policy_hash: str = Field(default="", max_length=256)
    level: int = Field(default=0)
    last_seen_scan_id: Optional[int] = Field(default=None)
    missing_count: int = Field(default=0)
    status: str = Field(default="active", max_length=32)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
