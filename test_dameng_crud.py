"""Quick CRUD test on Dameng"""
import datetime
from app.models.base import get_session
from app.models.scope_config import SyncScopeConfig
from app.models.scan_run import SyncScanRun
from sqlmodel import select, func

print("=== Dameng CRUD Test ===")

with get_session() as s:
    # 1. INSERT
    scope = SyncScopeConfig(
        tenant_id=1, source_type="knowledge_doc_lib",
        source_id="gns://test_crud", source_name="CRUD测试", enabled=True)
    s.add(scope)
    s.commit()
    print(f"INSERT: id={scope.id}")

    # 2. SELECT (via custom exec)
    result = s.exec(select(SyncScopeConfig).where(
        SyncScopeConfig.source_id == "gns://test_crud"))
    found = result.first()
    if isinstance(found, dict):
        print(f"SELECT(dict): source_name={found.get('source_name','?')} id={found.get('id','?')}")
    else:
        print(f"SELECT(obj): source_name={found.source_name} id={found.id}")

    # 3. COUNT
    from sqlmodel import func as f
    result2 = s.exec(select(f.count()).select_from(SyncScopeConfig))
    cnt = result2.one()
    print(f"COUNT: {cnt}")

    # 4. INSERT with datetime
    scan = SyncScanRun(
        tenant_id=1, scan_type="manual", scope_config_id=scope.id,
        total_files=9, new_files=9, status="completed",
        started_at=datetime.datetime.now(), completed_at=datetime.datetime.now())
    s.add(scan)
    s.commit()
    print(f"INSERT datetime: id={scan.id}")

    # 5. UPDATE
    found2 = s.exec(select(SyncScopeConfig).where(
        SyncScopeConfig.source_id == "gns://test_crud")).first()
    print(f"Before UPDATE: source_name={found2.source_name}")
    found2.source_name = "CRUD测试_已修改"
    s.commit()
    print(f"After UPDATE: source_name={found2.source_name}")

    # Verify
    found3 = s.exec(select(SyncScopeConfig).where(
        SyncScopeConfig.source_id == "gns://test_crud")).first()
    print(f"Verify UPDATE: source_name={found3.source_name}")

    # Cleanup
    s.delete(scan)
    s.delete(found2)
    s.commit()
    print("Cleanup done")

print("\n>>> All CRUD passed! <<<")
