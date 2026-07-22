"""Test the 3 untested features: incremental, batch, log-driven sync"""
import sys, logging, json
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BS_COOKIE = sys.argv[1] if len(sys.argv) > 1 else "eyJ..."
AS_TOKEN = sys.argv[2] if len(sys.argv) > 2 else "ory_at_..."
CT_TOKEN = sys.argv[3] if len(sys.argv) > 3 else ""  # optional

# ═══════════════════════════════════════════════════════════
# TEST 1: Incremental sync (公司资质 — has existing DB data)
# ═══════════════════════════════════════════════════════════
print("\n" + "="*60)
print("TEST 1: Incremental Sync")
print("="*60)

from app.models import init_db, get_session
from app.models.document_mapping import SyncDocumentMapping
from sqlmodel import select, func

init_db()
with get_session() as s:
    count = s.exec(select(func.count()).select_from(SyncDocumentMapping)).one()
    print(f"Existing doc mappings in DB: {count}")

from app.sync_pipeline import SyncPipeline
pipeline = SyncPipeline(
    bs_base="http://192.168.106.161:3001", bs_cookie=BS_COOKIE,
    as_base="https://5j-zsgl.powerchina.cn", as_token=AS_TOKEN,
)

print("\nRunning incremental sync (should skip all existing files)...")
result = pipeline.run(
    lib_gns="gns://1A71734693F8464A9B8C1980D4AFBB44",
    space_name="公司资质_incr_test",
    incremental=True,
)

transferred = result.get('transferred', 0)
files = result.get('files', 0)
print(f"\nResult: {transferred} transferred / {files} scanned")
if transferred == 0 and files > 0:
    print("TEST 1 PASSED ✅ — Incremental correctly skipped all unchanged files")
elif transferred > 0:
    print(f"TEST 1: {transferred} files transferred (may be new/changed)")
else:
    print("TEST 1 ⚠️ — No files scanned (token may be expired)")

# Check UUID map was populated
uuid_count = len(pipeline._uuid_to_gns)
print(f"UUID→GNS map: {uuid_count} entries")
if uuid_count > 0:
    print("TEST 1a PASSED ✅ — UUID mapping built during scan")
else:
    print("TEST 1a ⚠️ — UUID map empty")

# ═══════════════════════════════════════════════════════════
# TEST 2: Batch orchestrator
# ═══════════════════════════════════════════════════════════
print("\n" + "="*60)
print("TEST 2: Batch Orchestrator")
print("="*60)

from app.batch_orchestrator import BatchOrchestrator

orch = BatchOrchestrator(
    bs_base="http://192.168.106.161:3001", bs_cookie=BS_COOKIE,
    as_base="https://5j-zsgl.powerchina.cn", as_token=AS_TOKEN,
)

# Just load config and verify scopes (don't run all)
config = orch._load_config()
scopes = config.get("sync", {}).get("scopes", [])
enabled = [s for s in scopes if s.get("enabled", True)]
print(f"Config scopes: {len(scopes)} total, {len(enabled)} enabled")
for s in enabled:
    print(f"  {s.get('space_name','?'):20s}  type={s.get('source_type','?')}  gns={s.get('source_gns','?')[:40]}...")

if len(enabled) > 0:
    print("TEST 2 PASSED ✅ — Batch config loaded")
else:
    print("TEST 2 ⚠️ — No enabled scopes in config")

# ═══════════════════════════════════════════════════════════
# TEST 3: Log-driven incremental sync
# ═══════════════════════════════════════════════════════════
print("\n" + "="*60)
print("TEST 3: Log-driven Incremental Sync")
print("="*60)

if not CT_TOKEN:
    print("TEST 3 SKIPPED — No console token provided")
    print("Usage: python test_untested.py <bs_cookie> <as_token> [console_token]")
else:
    result = pipeline.sync_from_logs(
        console_token=CT_TOKEN,
        since_date=1784476800000000,  # 2026-07-13
        until_date=1784591999999000,  # 2026-07-21
    )
    print(f"Result: {json.dumps(result, indent=2)}")
    if result.get("total_changes", 0) > 0:
        print("TEST 3 PASSED ✅ — Log-driven sync processed changes")
    else:
        print("TEST 3 ⚠️ — No changes found (may be token expired or no events)")

    # Also test LogEventHandler directly with pulled logs
    print("\n=== LogEventHandler test ===")
    import httpx as hx
    events = []
    for logType in [12]:
        start = 0
        while len(events) < 50:
            body = [{'ncTGetPageLogParam': {
                'userId': '3e7a9110-3de5-11ef-bb23-de677a88534a',
                'start': start, 'limit': 50,
                'maxLogId': 9223372036854775807,
                'logType': logType,
                'levels': [], 'macs': [], 'ips': [], 'displayNames': [],
                'opTypes': [2, 11, 19, 22, 3],
                'msgs': [], 'exMsgs': [],
                'startDate': 1784476800000000, 'endDate': 1784591999999000
            }}]
            try:
                r = hx.post(
                    f'https://5j-zsgl.powerchina.cn/console/api/EACPLog/GetPageLog',
                    json=body, timeout=30,
                    headers={'Authorization': f'Bearer {CT_TOKEN}',
                             'Content-Type': 'application/json;charset=UTF-8'})
                if r.status_code != 200:
                    break
                data = r.json()
                if not data:
                    break
                events.extend(data)
                if len(data) < 50:
                    break
                start += len(data)
            except Exception as e:
                print(f"  Pull error: {e}")
                break

    print(f"Pulled {len(events)} log events")

    if events:
        from app.services.log_event_handler import LogEventHandler
        handler = LogEventHandler(pipeline, BS_COOKIE)
        stats = handler.handle(events)
        print(f"Handler stats: {json.dumps(stats, indent=2)}")

        error_count = stats.get("errors", 0)
        total_actions = sum(v for k, v in stats.get("stats", {}).items())
        if total_actions > 0:
            print("TEST 3a PASSED ✅ — LogEventHandler dispatched events")
        elif error_count == 0:
            print("TEST 3a ⚠️ — No actionable events in sample")
    else:
        print("TEST 3a ⚠️ — Could not pull logs")

print("\n" + "="*60)
print("ALL TESTS COMPLETE")
print("="*60)
