"""Test database models create OK."""

import pytest
from sqlmodel import SQLModel, create_engine, Session

from app.models import (
    SyncScopeConfig, SyncPrincipalMapping, SyncSpaceMapping,
    SyncFolderMapping, SyncDocumentMapping, SyncScanRun,
    SyncTask, SyncPermissionSnapshot, SyncAuditEvent,
)


@pytest.fixture(scope="module")
def engine():
    e = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(e)
    return e


def test_create_all_tables(engine):
    """Smoke test: one row in each table."""
    with Session(engine) as s:
        s.add(SyncScopeConfig(source_type="user_doc_lib", source_id="gns://test"))
        s.add(SyncPrincipalMapping(source_id="u1", source_type="user", source_name="test"))
        s.add(SyncSpaceMapping(source_doc_lib_id="gns://lib1", source_doc_lib_name="lib1", source_type="personal"))
        s.add(SyncFolderMapping(space_mapping_id=1, source_folder_id="gns://f1", source_name="f1"))
        s.add(SyncDocumentMapping(space_mapping_id=1, source_doc_id="gns://doc1", source_name="doc1"))
        s.add(SyncScanRun())
        s.add(SyncTask(idempotency_key="abc123", action="transfer_create", source_doc_id="gns://doc1"))
        s.add(SyncPermissionSnapshot(resource_type="space", resource_id="1", source_acl_raw="{}"))
        s.add(SyncAuditEvent(trace_id="t1", action="scan", result="success"))
        s.commit()

    with Session(engine) as s:
        from sqlmodel import select, func
        assert s.exec(select(func.count()).select_from(SyncScopeConfig)).one() == 1
        assert s.exec(select(func.count()).select_from(SyncTask)).one() == 1
        assert s.exec(select(func.count()).select_from(SyncAuditEvent)).one() == 1
