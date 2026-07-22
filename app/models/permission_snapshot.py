"""Permission snapshot — raw ACL + translated FGA tuples."""

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class SyncPermissionSnapshot(SQLModel, table=True):
    __tablename__ = "anyshare_sync_permission_snapshot"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(default=1, index=True)
    space_mapping_id: Optional[int] = Field(default=None, index=True)
    document_mapping_id: Optional[int] = Field(default=None, index=True)
    resource_type: str = Field(max_length=32)
    resource_id: str = Field(max_length=1024)
    source_acl_raw: str = Field(default="{}", max_length=65536)
    target_fga_tuples: str = Field(default="[]", max_length=65536)
    policy_hash: str = Field(default="", max_length=256, index=True)
    is_blocked: bool = Field(default=False)
    block_reason: str = Field(default="", max_length=1024)
    created_at: datetime = Field(default_factory=datetime.now)
