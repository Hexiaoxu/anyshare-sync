"""Document mapping table — AnyShare file → BISHENG KnowledgeFile."""

from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class SyncDocumentMapping(SQLModel, table=True):
    __tablename__ = "anyshare_sync_document_mapping"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(default=1, index=True)
    space_mapping_id: int = Field(index=True)
    folder_mapping_id: Optional[int] = Field(default=None, index=True)
    source_doc_id: str = Field(max_length=1024, unique=True)
    source_rev: str = Field(default="", max_length=256)
    source_name: str = Field(max_length=512)
    source_size: int = Field(default=0)
    content_version: str = Field(default="", max_length=256)
    metadata_hash: str = Field(default="", max_length=256)
    policy_hash: str = Field(default="", max_length=256)
    target_file_id: Optional[int] = Field(default=None, index=True)
    target_document_id: Optional[int] = Field(default=None)
    target_version_id: Optional[int] = Field(default=None)
    target_upload_ref: Optional[str] = Field(default=None, max_length=4096)
    idempotency_key: str = Field(default="", max_length=256, index=True)
    current_action: str = Field(default="", max_length=32)
    status: str = Field(default="discovered", max_length=32)
    next_check_at: Optional[datetime] = Field(default=None)
    last_seen_scan_id: Optional[int] = Field(default=None)
    missing_count: int = Field(default=0)
    retry_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
