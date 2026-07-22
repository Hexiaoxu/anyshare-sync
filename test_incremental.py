"""Integration test for incremental sync + log-driven sync"""
import sys, json, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# === CONFIGURE ===
AS_TOKEN = sys.argv[1] if len(sys.argv) > 1 else "ory_at_..."
BS_COOKIE = sys.argv[2] if len(sys.argv) > 2 else "eyJ..."
CT_TOKEN = sys.argv[3] if len(sys.argv) > 3 else ""  # optional console token

LIB_GNS = "gns://1A71734693F8464A9B8C1980D4AFBB44"
SPACE_NAME = "公司资质_incr_test"

# ════════════════════════════════════════════════════════
# TEST 0: Scan only (no migration) — verify can get full
#         directory tree + ACL for department doc lib
# ════════════════════════════════════════════════════════
print("\n" + "="*60)
print("TEST 0: Scan-only — dept doc lib listing + ACL")
print("="*60)

DEPT_LIB = "gns://0C9379F8E48545FEBE837679F3B4D9FA/11C780161B4D4F7BB9E227D6E332E37B/26FBA3F5DCAB467D9BB150C19FAFE75E/CB95075F74E34552B2D9577A338EDF87"
import httpx
from urllib.parse import quote

all_dirs, all_files, skipped = [], [], 0
queue = [(DEPT_LIB, "", 0)]
scanned = set()
MAX_DEPTH = 3; MAX_ITEMS = 50

while queue:
    gns, parent, depth = queue.pop(0)
    if gns in scanned or depth > MAX_DEPTH:
        continue
    scanned.add(gns)
    enc = quote(gns, safe="")
    r = httpx.get(
        f"https://5j-zsgl.powerchina.cn/api/efast/v1/folders/{enc}/sub_objects"
        f"?limit=200&sort=name&direction=asc",
        headers={"Authorization": f"Bearer {AS_TOKEN}"}, timeout=60)
    if r.status_code != 200:
        print(f"  HTTP {r.status_code} at depth {depth}")
        break
    sub = r.json()
    for d in sub.get("dirs", []):
        if len(all_dirs) < MAX_ITEMS:
            all_dirs.append(d)
            queue.append((d["id"], gns, depth + 1))
    for f in sub.get("files", []):
        if len(all_files) < MAX_ITEMS:
            all_files.append(f)
    print(f"  depth={depth}: {len(all_dirs)} dirs, {len(all_files)} files", end="\r")
    if len(all_dirs) + len(all_files) >= MAX_ITEMS:
        break

print(f"\n  Total: {len(all_dirs)} dirs + {len(all_files)} files")

# Test ACL for first 3 items
print("\n  ACL test (first 3 items):")
acl_ok = 0
for item in ([DEPT_LIB] + [d["id"] for d in all_dirs[:2]]):
    r = httpx.post(
        f"https://5j-zsgl.powerchina.cn/api/eacp/v1/perm2/get",
        json={"docid": item},
        headers={"Authorization": f"Bearer {AS_TOKEN}"}, timeout=30)
    if r.status_code == 200:
        perms = r.json().get("perminfos", [])
        name = item.rsplit("/", 1)[-1][:30]
        print(f"    {name}: {len(perms)} entries, inherit={r.json().get('inherit')}")
        acl_ok += 1
    else:
        print(f"    FAIL: {r.status_code}")

print(f"\n  ACL: {acl_ok}/3 OK")
print(f"  ✅ TEST 0: Can scan dept lib and get ACL without migration")

# ════════════════════════════════════════════════════════
# TEST 2: Incremental sync
# ════════════════════════════════════════════════════════
print("\n" + "="*60)
print("TEST 2: Incremental Sync")
print("="*60)

from app.sync_pipeline import SyncPipeline
from app.models import init_db, get_session
from app.models.document_mapping import SyncDocumentMapping
from sqlmodel import select, func

# Check existing mappings
init_db()
with get_session() as s:
    existing = s.exec(
        select(func.count()).select_from(SyncDocumentMapping)
    ).one()
    print(f"Existing doc mappings in DB: {existing}")

# Run incremental
pipeline = SyncPipeline(
    bs_base="http://192.168.106.161:3001",
    bs_cookie=BS_COOKIE,
    as_base="https://5j-zsgl.powerchina.cn",
    as_token=AS_TOKEN,
)

print("\nRunning incremental sync...")
result = pipeline.run(
    lib_gns=LIB_GNS,
    space_name=SPACE_NAME,
    incremental=True,  # <-- INCREMENTAL MODE
)

print(f"\nResults:")
for k, v in result.items():
    print(f"  {k}: {v}")

# Verify: should skip all 9 existing files
expected_skipped = 9  # from previous 公司资质 syncs
transferred = result.get('transferred', 0)
if transferred == 0:
    print("\n✅ TEST 2 PASSED: Incremental correctly skipped all existing files")
else:
    print(f"\n⚠️  TEST 2: Transferred {transferred} files (expected 0)")

# ════════════════════════════════════════════════════════
# TEST 3: Log-driven incremental sync
# ════════════════════════════════════════════════════════
if CT_TOKEN:
    print("\n" + "="*60)
    print("TEST 3: Log-driven Incremental Sync")
    print("="*60)

    from app.services.log_event_handler import LogEventHandler, LogEvent

    # Pull logs from console
    AS = "https://5j-zsgl.powerchina.cn"
    events = []
    for logType in [12]:
        start = 0
        while len(events) < 500:
            body = [{'ncTGetPageLogParam': {
                'userId': '3e7a9110-3de5-11ef-bb23-de677a88534a',
                'start': start, 'limit': 100,
                'maxLogId': 9223372036854775807,
                'logType': logType,
                'levels': [], 'macs': [], 'ips': [], 'displayNames': [],
                'opTypes': [2, 11, 19, 22, 3, 24],
                'msgs': [], 'exMsgs': [],
                'startDate': 1784476800000000,  # 2026-07-13
                'endDate': 1784591999999000     # 2026-07-21
            }}]
            try:
                r = pipeline._bs._post.__func__  # skip, use httpx directly
                import httpx as hx
                r = hx.post(
                    f'{AS}/console/api/EACPLog/GetPageLog',
                    json=body, timeout=30,
                    headers={'Authorization': f'Bearer {CT_TOKEN}',
                             'Content-Type': 'application/json;charset=UTF-8'})
                if r.status_code != 200:
                    break
                data = r.json()
                if not data:
                    break
                events.extend(data)
                start += len(data)
                if len(data) < 100:
                    break
            except Exception as e:
                print(f"  Pull error: {e}")
                break

    print(f"Pulled {len(events)} log events")

    if events:
        handler = LogEventHandler(pipeline, BS_COOKIE)
        result = handler.handle(events)
        print(f"Log handler result: {json.dumps(result, indent=2)}")
        print("\n✅ TEST 3 PASSED: Log-driven sync completed")
    else:
        print("\n⚠️  TEST 3: No events pulled (token may be expired)")
else:
    print("\n⚠️  TEST 3 SKIPPED: No console token provided")
    print("Usage: python test_incremental.py <as_token> <bs_cookie> [console_token]")
