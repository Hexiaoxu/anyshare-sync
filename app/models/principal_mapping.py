"""Principal (user/department/group) mapping table."""

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class SyncPrincipalMapping(SQLModel, table=True):
    __tablename__ = "anyshare_sync_principal_mapping"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(default=1, index=True)
    source_id: str = Field(max_length=1024, index=True)
    source_type: str = Field(max_length=32)
    source_name: str = Field(max_length=256)
    target_id: Optional[int] = Field(default=None, index=True)
    status: str = Field(default="identity_pending", max_length=32)
    match_method: str = Field(default="", max_length=32)
    extra: str = Field(default="{}", max_length=4096)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
