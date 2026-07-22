"""Document library → Knowledge Space mapping."""

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class SyncSpaceMapping(SQLModel, table=True):
    __tablename__ = "anyshare_sync_space_mapping"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(default=1, index=True)
    source_doc_lib_id: str = Field(max_length=1024, unique=True)
    source_doc_lib_name: str = Field(max_length=256)
    source_type: str = Field(max_length=32)
    source_owner_id: Optional[str] = Field(default=None, max_length=1024)
    target_space_id: Optional[int] = Field(default=None, index=True)
    status: str = Field(default="pending", max_length=32)
    auth_type: str = Field(default="public", max_length=16)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
