"""CLI for AnyShare → BISHENG sync — one entry for all three doc lib types.

=== Quick reference ===

List all knowledge / department / personal libraries:
    python run_sync.py <as_token> <bs_cookie> --list <knowledge|department|personal>

Single scope:
    python run_sync.py <as_token> <bs_cookie> <lib_gns> <space_name>
        [--type knowledge|department|personal]
        [--ancestors "a,b,c"] [--skip-download] [--incremental]
        [--grant-owner <username>]

Personal lib (auto find+token+grant owner):
    python run_sync.py <bs_cookie> --user <name/keyword> [--token <user_token>]
        [--incremental]
    If --token omitted, tries to get token for <name>.

Batch mode (reads config.yaml scopes):
    python run_sync.py <as_token> <bs_cookie> --batch
        [--incremental]

Tree mode (one space, many sub-folders via config.yaml trees):
    python run_sync.py <as_token> <bs_cookie> --tree

Log-driven incremental (pull console EACPLog → sync only changed items):
    python run_sync.py <as_token> <bs_cookie> <lib_gns> <space_name>
        --sync-logs <console_token> <since_us> <until_us>

Auto-sync daemon (pull logs every hour, apply changes):
    python run_sync.py <as_token> <bs_cookie> <lib_gns> <space_name>
        --daemon <console_token> [--interval 3600]
"""
import sys
from app.logger import setup_logging, get_logger, set_trace_id

setup_logging()
logger = get_logger("run_sync")

BS_BASE = "http://your-bisheng.example.com:3001"
AS_BASE = "https://your-anyshare.example.com"

# Create AnyShareAuth once (used for auto-refresh token)
from app.connectors.anyshare.auth import AnyShareAuth
_as_auth = AnyShareAuth(AS_BASE,
    "your-client-id", "your-client-secret")

# ── Parse args ──────────────────────────────────────────────

if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
    print(__doc__)
    sys.exit(0)

user_mode = "--user" in sys.argv
batch_mode = "--batch" in sys.argv
tree_mode = "--tree" in sys.argv
incremental = "--incremental" in sys.argv
sync_logs = "--sync-logs" in sys.argv
daemon_mode = "--daemon" in sys.argv
list_mode = "--list" in sys.argv

if user_mode:
    # --user mode: python run_sync.py <bs_cookie> --user <name> [--token <t>]
    bs_cookie = sys.argv[1]
    as_token = ""  # will be set after getting user token
    args = sys.argv[2:]
else:
    as_token = sys.argv[1]
    bs_cookie = sys.argv[2] if len(sys.argv) > 2 else ""
    args = sys.argv[3:]

# ── List mode ───────────────────────────────────────────────

if list_mode:
    list_type = "knowledge"
    try:
        idx = args.index("--list")
        if idx + 1 < len(args):
            list_type = args[idx + 1]
    except ValueError:
        pass
    import httpx
    from urllib.parse import quote
    type_map = {
        "knowledge": "/api/efast/v1/doc-lib/knowledge",
        "department": "/api/efast/v1/doc-lib/department",
        "personal": "/api/efast/v1/doc-lib/user",
    }
    url = f"{AS_BASE}{type_map.get(list_type, type_map['knowledge'])}"
    print(f"\n=== {list_type} doc libs ===")
    offset = 0
    while True:
        r = httpx.get(f"{url}?offset={offset}&limit=200",
                      headers={"Authorization": f"Bearer {as_token}"}, timeout=60)
        if r.status_code != 200:
            print(f"HTTP {r.status_code} — token may be expired")
            break
        entries = r.json().get("entries", [])
        if not entries:
            break
        for e in entries:
            name = e.get("name", "?")
            if list_type == "personal":
                owners = e.get("owned_by", [])
                owner = owners[0]["name"] if owners else "?"
                print(f"  {owner:20s}  {name[:40]}  {e['id']}")
            else:
                print(f"  {name[:40]:40s}  {e['id']}")
        offset += len(entries)
        if len(entries) < 200:
            break
    print(f"\nTotal: {offset}")
    sys.exit(0)

# ── Batch mode ──────────────────────────────────────────────

if batch_mode:
    from app.batch_orchestrator import BatchOrchestrator
    orch = BatchOrchestrator(
        bs_base=BS_BASE, bs_cookie=bs_cookie,
        as_base=AS_BASE, as_token=as_token,
        as_auth=_as_auth,
    )
    results = orch.run_all(incremental=incremental)

    print(f"\n=== Batch Results ===")
    for r in results:
        status = "FAIL" if r.get("error") else "OK"
        print(f"  [{status}] {r.get('space_name','?'):30s}  "
              f"files={r.get('files',0):>4}  "
              f"xfer={r.get('transferred',0):>4}  "
              f"ACL={str(r.get('acl_synced','?')):>6s}  "
              f"{r.get('elapsed_sec',0):.0f}s"
              + (f"  ERR={r['error'][:60]}" if r.get('error') else ""))
    sys.exit(0)

# ── Tree mode ────────────────────────────────────────────────

if tree_mode:
    from app.tree_orchestrator import TreeOrchestrator
    orch = TreeOrchestrator(
        bs_base=BS_BASE, bs_cookie=bs_cookie,
        as_base=AS_BASE, as_token=as_token,
        as_auth=_as_auth,
    )
    results = orch.run_all()

    print(f"\n=== Tree Results ===")
    for r in results:
        status = "FAIL" if r.get("error") else "OK"
        print(f"  [{status}] {r.get('tree','?'):15s}/{r.get('item','?'):30s}  "
              f"dirs={r.get('dirs',0):>4}  ACL={str(r.get('acl_synced','?')):>6s}  "
              f"{r.get('elapsed_sec',0):.0f}s"
              + (f"  ERR={r['error'][:60]}" if r.get('error') else ""))
    sys.exit(0)

# ── Single scope ────────────────────────────────────────────

if len(args) < 2:
    print("Single mode needs: <lib_gns> <space_name>")
    sys.exit(1)

lib_gns = args[0]
space_name = args[1]
source_type = "knowledge_doc_lib"
ancestors = None
skip_download = False
no_root_perms = False
grant_owner = None
console_token = ""
since_us = 0
until_us = 0

i = 2
while i < len(args):
    if args[i] == "--type" and i + 1 < len(args):
        source_type = args[i + 1]; i += 2
    elif args[i] == "--ancestors" and i + 1 < len(args):
        ancestors = args[i + 1].split(","); i += 2
    elif args[i] == "--skip-download":
        skip_download = True; i += 1
    elif args[i] == "--no-root-perms":
        no_root_perms = True; i += 1
    elif args[i] == "--grant-owner" and i + 1 < len(args):
        grant_owner = args[i + 1]; i += 2
    elif args[i] == "--sync-logs" and i + 3 < len(args):
        console_token = args[i + 1]
        since_us = int(args[i + 2])
        until_us = int(args[i + 3])
        i += 4
    else:
        i += 1

from app.sync_pipeline import SyncPipeline

pipeline = SyncPipeline(
    bs_base=BS_BASE, bs_cookie=bs_cookie,
    as_base=AS_BASE, as_token=as_token,
    as_auth=_as_auth,
)

# ── Log-driven incremental mode ─────────────────────────────

if sync_logs and console_token:
    print(f"\n=== Log-driven incremental sync ===")
    result = pipeline.sync_from_logs(
        console_token=console_token,
        since_date=since_us,
        until_date=until_us,
    )
    print(f"\n=== Result ===")
    for k, v in result.items():
        print(f"  {k}: {v}")
    sys.exit(0)

# ── Daemon mode ──────────────────────────────────────────────

if daemon_mode:
    console_token = ""
    interval = 3600
    for i, a in enumerate(sys.argv):
        if a == "--daemon" and i + 1 < len(sys.argv):
            console_token = sys.argv[i + 1]
        if a == "--interval" and i + 1 < len(sys.argv):
            try:
                interval = int(sys.argv[i + 1])
            except ValueError:
                pass

    if not console_token:
        print("--daemon requires a console token")
        sys.exit(1)

    lib_gns = args[0] if len(args) > 0 else ""
    space_name = args[1] if len(args) > 1 else ""
    if not lib_gns or not space_name:
        print("Daemon mode needs: <as_token> <bs_cookie> <lib_gns> <space_name> --daemon <console_token>")
        sys.exit(1)

    from app.services.log_scheduler import LogSyncScheduler
    scheduler = LogSyncScheduler(
        pipeline=pipeline, console_token=console_token,
        bs_cookie=bs_cookie, interval=interval,
    )

    # Run one initial full sync to build UUID mapping
    print("=== Initial full sync to build UUID mapping ===")
    result = pipeline.run(lib_gns=lib_gns, space_name=space_name,
                          incremental=False)
    print(f"Full sync done: {result.get('files', 0)} files")

    # Start daemon loop
    print(f"\n=== Starting daemon (interval={interval}s) ===")
    print("Press Ctrl+C to stop")
    try:
        scheduler.run_forever()
    except KeyboardInterrupt:
        scheduler.stop()
        print("\nDaemon stopped")
    sys.exit(0)

# ── Personal lib mode: --user <username> ───────────────────

if user_mode:
    idx = args.index("--user")
    # Optional --token for manual token injection
    token_override = None
    if "--token" in args:
        ti = args.index("--token")
        if ti + 1 < len(args):
            token_override = args[ti + 1]

    username = args[idx + 1] if idx + 1 < len(args) else None
    if not username:
        print("--user requires a username (e.g. 5jchenbo or 程博)")
        sys.exit(1)

    from app.connectors.anyshare.auth import AnyShareAuth
    auth = AnyShareAuth(AS_BASE, "7b98e7b6-f35e-4613-aeed-5b13112b0ff8", "Test123.")

    # Find personal lib GNS (use app token to search all users)
    print(f"Finding personal lib...")
    import httpx as hx
    app_token = auth.get_app_token()
    offset = 0
    found_gns = None
    found_name = username
    while offset < 9000:
        r = hx.get(
            f"{AS_BASE}/api/efast/v1/doc-lib/user?offset={offset}&limit=200",
            headers={"Authorization": f"Bearer {app_token}"}, timeout=60)
        if r.status_code != 200:
            break
        for e in r.json().get("entries", []):
            owners = e.get("owned_by", [])
            if owners:
                oname = owners[0].get("name", "")
                oid = owners[0].get("id", "")
                # Match: exact, substring, or partial (strip number prefix)
                kw = username.lower()
                # Also try meaningful part: "5jchenbo" -> "chenbo"
                import re
                m = re.search(r'[a-z]{2,}', kw)
                kw_core = m.group() if m else ""
                if (kw in oname.lower() or kw in oid.lower()
                        or (kw_core and kw_core in oname.lower())):
                    found_gns = e["id"]
                    found_name = oname
                    break
        if found_gns:
            break
        offset += 200
        if len(r.json().get("entries", [])) < 200:
            break

    if not found_gns:
        print(f"User '{username}' has no personal doc lib")
        sys.exit(1)

    # Try to get user token: override > username > display name
    user_token = None
    if token_override:
        user_token = token_override
        print("Using provided token")
    else:
        for account in [username, found_name]:
            try:
                user_token = auth.get_user_token(account)
                print(f"Got token via account: {account}")
                break
            except Exception:
                continue

    if not user_token:
        print(f"Cannot get user token for {username} or {found_name} (no password set)")
        sys.exit(1)

    as_token = user_token

    print(f"Found: {found_name} -> {found_gns}")
    lib_gns = found_gns
    space_name = f"{found_name}_个人库"
    source_type = "user_doc_lib"
    grant_owner = found_name

    # Recreate pipeline with correct token (don't use app token for personal libs)
    pipeline = SyncPipeline(
        bs_base=BS_BASE, bs_cookie=bs_cookie,
        as_base=AS_BASE, as_token=as_token,
        as_auth=None,  # personal lib → don't auto-refresh
    )

# ── Full / incremental sync ─────────────────────────────────

result = pipeline.run(
    lib_gns=lib_gns, space_name=space_name,
    ancestors=ancestors, skip_download=skip_download,
    source_type=source_type, incremental=incremental,
    grant_owner=grant_owner,
    no_root_perms=no_root_perms,
)

print(f"\n=== Result ===")
for k, v in result.items():
    print(f"  {k}: {v}")
if result.get("space_id"):
    print(f"View: http://192.168.106.161:3001 → {space_name} (id={result['space_id']})")
