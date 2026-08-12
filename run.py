"""One-click sync — fully automatic, all config from config.yaml.

Usage:
    python run.py                               # Interactive menu
    python run.py --tree                        # Tree sync (knowledge + dept)
    python run.py --user <username>             # Personal lib sync
    python run.py --batch                       # Batch from config.yaml
    python run.py --daemon                      # Hourly incremental daemon
    python run.py --sync-org                    # Sync org via AnyShare API
    python run.py --import-org                  # Import org from Excel (config excel_path)
    python run.py --import-org <file.xlsx>      # Import org from specified Excel file
    python run.py --sync-personal <user>        # Migrate one user's personal lib
    python run.py --sync-dept <name> <gns>      # Migrate one dept lib (folders+perms only)
    python run.py --sync-dept <name> <gns> --with-files  # Also migrate files
    python run.py <gns> <space_name>            # Single scope full sync
"""
import sys, os, logging, time
from pathlib import Path
import yaml

from app.logger import setup_logging, get_logger, set_trace_id

setup_logging()
logger = get_logger("run")

# ── Load config ────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / "config" / "config.yaml"
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    _cfg = yaml.safe_load(f)

BS_BASE = _cfg["bisheng"]["base_url"]
AS_BASE = _cfg["anyshare"]["base_url"]
CLIENT_ID = _cfg["anyshare"]["client_id"]
CLIENT_SECRET = _cfg["anyshare"]["client_secret"]
ADMIN_ACCOUNT = _cfg["anyshare"]["admin_account"]

# ── Auto-create auth ─────────────────────────────────────────

from app.connectors.anyshare.auth import AnyShareAuth
auth = AnyShareAuth(AS_BASE, CLIENT_ID, CLIENT_SECRET)

def get_as_token():
    try:
        return auth.get_user_token(ADMIN_ACCOUNT)
    except Exception as e:
        logger.error(f"AnyShare token failed: {e}")
        sys.exit(1)

# ── Parse args ───────────────────────────────────────────────

args = sys.argv[1:]
tree_mode = "--tree" in args
batch_mode = "--batch" in args
daemon_mode = "--daemon" in args
user_mode = "--user" in args
list_mode = "--list" in args
incremental = "--incremental" in args

# ── List mode ─────────────────────────────────────────────────

if list_mode:
    list_type = "knowledge"
    try:
        idx = args.index("--list")
        if idx + 1 < len(args): list_type = args[idx + 1]
    except: pass
    import httpx
    url_map = {"knowledge": "/api/efast/v1/doc-lib/knowledge",
               "department": "/api/efast/v1/doc-lib/department",
               "personal": "/api/efast/v1/doc-lib/user"}
    token = get_as_token()
    print(f"\n=== {list_type} doc libs ===")
    r = httpx.get(f"{AS_BASE}{url_map.get(list_type, url_map['knowledge'])}?offset=0&limit=200",
                  headers={"Authorization": f"Bearer {token}"}, timeout=60)
    for e in r.json().get('entries', [])[:30]:
        name = e.get('name', '?')
        if list_type == "personal":
            owners = e.get('owned_by', [])
            owner = owners[0]['name'] if owners else '?'
            print(f"  {owner:20s}  {e['id']}")
        else:
            print(f"  {name[:40]:40s}  {e['id']}")
    sys.exit(0)

# ── Tree mode ─────────────────────────────────────────────────

if tree_mode:
    from app.tree_orchestrator import TreeOrchestrator
    orch = TreeOrchestrator(
        bs_base=BS_BASE, bs_cookie="", as_base=AS_BASE,
        as_token=get_as_token(), as_auth=auth, as_account=ADMIN_ACCOUNT)
    results = orch.run_all()
    ok = sum(1 for r in results if not r.get("error"))
    print(f"\n=== Tree: {ok}/{len(results)} OK ===")
    for r in results:
        s = "FAIL" if r.get("error") else "OK"
        print(f"  [{s}] {r.get('tree','?')}/{r.get('item','?')}: "
              f"D={r.get('dirs',0)} ACL={r.get('acl_synced','?')}")
    sys.exit(0)

# ── Batch mode ────────────────────────────────────────────────

if batch_mode:
    from app.batch_orchestrator import BatchOrchestrator
    orch = BatchOrchestrator(
        bs_base=BS_BASE, bs_cookie="", as_base=AS_BASE,
        as_token=get_as_token(), as_auth=auth)
    results = orch.run_all(incremental=incremental)
    ok = sum(1 for r in results if not r.get("error"))
    print(f"\n=== Batch: {ok}/{len(results)} OK ===")
    sys.exit(0)

# ── User mode ─────────────────────────────────────────────────

if user_mode:
    idx = args.index("--user")
    username = args[idx + 1] if idx + 1 < len(args) else None
    if not username:
        print("--user requires a username"); sys.exit(1)

    # Get user token
    try:
        user_token = auth.get_user_token(username)
    except:
        print(f"Cannot get token for {username}")
        sys.exit(1)

    # Find personal lib GNS
    app_token = auth.get_app_token()
    import httpx as hx
    import urllib.parse
    found_gns = None
    found_name = username
    offset = 0
    while offset < 9000:
        r = hx.get(f'{AS_BASE}/api/efast/v1/doc-lib/user?offset={offset}&limit=200',
                   headers={'Authorization': f'Bearer {app_token}'}, timeout=60)
        if r.status_code != 200: break
        for e in r.json().get('entries', []):
            owners = e.get('owned_by', [])
            if owners:
                oname = owners[0].get('name', '')
                oid = owners[0].get('id', '')
                kw = username.lower()
                if (kw == oname.lower() or kw == oid.lower()
                        or kw in oname.lower()):
                    found_gns = e['id']; found_name = owners[0]['name']; break
        if found_gns: break
        offset += 200
        if len(r.json().get('entries', [])) < 200: break

    if not found_gns:
        # Fallback: scan department API to find display name for this account
        print(f"Looking up display name for {username}...")
        try:
            admin_token = auth.get_user_token('5jliming1')
            r_roots = hx.post(f'{AS_BASE}/api/eacp/v1/department/getroots', json={},
                              headers={'Authorization': f'Bearer {admin_token}',
                                       'Content-Type': 'application/json'}, timeout=15)
            roots = r_roots.json().get('depinfos', [])
            q = [(d['depid'], d['name']) for d in roots]
            display_name = None
            while q:
                did, dname = q.pop(0)
                ru = hx.post(f'{AS_BASE}/api/eacp/v1/department/getsubusers',
                             json={'depid': did}, headers={'Authorization': f'Bearer {admin_token}',
                             'Content-Type': 'application/json'}, timeout=10)
                for u in (ru.json().get('userinfos', []) if ru.status_code == 200 else []):
                    if u.get('account') == username:
                        display_name = u.get('name'); break
                if display_name: break
                rs = hx.post(f'{AS_BASE}/api/eacp/v1/department/getsubdeps',
                             json={'depid': did}, headers={'Authorization': f'Bearer {admin_token}',
                             'Content-Type': 'application/json'}, timeout=10)
                for s in (rs.json().get('depinfos', []) if rs.status_code == 200 else []):
                    q.append((s['depid'], s['name']))
            if display_name:
                print(f"  Display name: {display_name}")
                # Re-search doc-lib/user with display name
                offset = 0
                while offset < 9000:
                    r2 = hx.get(f'{AS_BASE}/api/efast/v1/doc-lib/user?offset={offset}&limit=200',
                                headers={'Authorization': f'Bearer {app_token}'}, timeout=60)
                    if r2.status_code != 200: break
                    for e in r2.json().get('entries', []):
                        owners = e.get('owned_by', [])
                        if owners and owners[0].get('name') == display_name:
                            found_gns = e['id']; found_name = display_name; break
                    if found_gns: break
                    offset += 200
                    if len(r2.json().get('entries', [])) < 200: break
        except Exception as e:
            print(f"  Lookup failed: {e}")
    if not found_gns:
        print(f"User '{username}' not found")
        sys.exit(1)

    print(f"User: {found_name}, GNS: {found_gns}")
    from app.sync_pipeline import SyncPipeline
    pipeline = SyncPipeline(BS_BASE, "", AS_BASE, user_token)
    result = pipeline.run(found_gns, f"{found_name}_个人库",
                          source_type="user_doc_lib", grant_owner=found_name)
    print(f"\nResult: {result}")
    sys.exit(0)

# ── Sync personal lib ────────────────────────────────────────

if "--sync-personal" in args:
    idx = args.index("--sync-personal")
    if idx + 1 >= len(args):
        print("Usage: python run.py --sync-personal <display_name>")
        print("  display_name: AnyShare 用户显示名（中文名）")
        sys.exit(1)

    target_display = args[idx + 1]
    import json as _json
    import subprocess

    # 从 users_import.json 找 username
    excel_json = Path(__file__).parent / "users_import.json"
    if not excel_json.exists():
        print(f"ERROR: users_import.json not found. Run --import-org first.")
        sys.exit(1)
    with open(excel_json, encoding="utf-8") as f:
        _users = _json.load(f)
    _user = next((u for u in _users if u["display"] == target_display), None)
    if not _user:
        print(f"ERROR: User '{target_display}' not found in users_import.json")
        sys.exit(1)

    from app.connectors.anyshare.auth import AnyShareAuth as _Auth
    from app.connectors.anyshare.doclib import AnyShareDocLib as _DocLib
    _auth = _Auth(AS_BASE, _cfg["anyshare"]["client_id"], _cfg["anyshare"]["client_secret"])
    _doclib = _DocLib(AS_BASE, _auth.get_app_token, _auth.get_user_token,
                      admin_account=ADMIN_ACCOUNT)
    _libs = _doclib.list_personal()
    _lib = next((l for l in _libs if l.owner_name == target_display), None)
    if not _lib:
        print(f"ERROR: No personal lib found for '{target_display}'")
        sys.exit(1)

    _as_token = _auth.get_user_token(_user["username"])
    from app.connectors.bisheng.token_generator import generate_bs_token as _gen_bs
    _bs_cookie = _gen_bs()

    print(f"=== Syncing personal lib: {target_display} ===")
    print(f"  GNS: {_lib.id}")
    ret = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "sync_one_user.py"),
         _as_token, _bs_cookie, _lib.id, target_display],
        timeout=1800
    )
    sys.exit(ret.returncode)


# ── Sync dept lib ─────────────────────────────────────────────

if "--sync-dept" in args:
    idx = args.index("--sync-dept")
    if idx + 2 >= len(args):
        print("Usage: python run.py --sync-dept <space_name> <gns> [--with-files]")
        print("  space_name: BISHENG 知识空间名（如：人力资源部）")
        print("  gns:        AnyShare 部门文档库 GNS")
        print("  --with-files: 同时迁移文件（默认只迁移文件夹+权限）")
        sys.exit(1)

    _dept_name = args[idx + 1]
    _dept_gns  = args[idx + 2]
    _with_files = "--with-files" in args

    import subprocess
    _script = Path(__file__).parent / "sync_dept_lib.py"
    _cmd = [sys.executable, str(_script)]
    if _with_files:
        _cmd.append("--with-files")

    # 注入参数给 sync_dept_lib.py（通过环境变量）
    import os as _os
    _env = _os.environ.copy()
    _env["DEPT_NAME"] = _dept_name
    _env["DEPT_GNS"]  = _dept_gns

    print(f"=== Syncing dept lib: {_dept_name} ===")
    print(f"  GNS: {_dept_gns}")
    print(f"  Files: {'enabled' if _with_files else 'disabled'}")
    ret = subprocess.run(_cmd, env=_env, timeout=7200)
    sys.exit(ret.returncode)


# ── Import org from Excel ────────────────────────────────────

if "--import-org" in args:
    from app.config import AppConfig
    from app.services.org_importer import OrgImporter

    cfg = AppConfig.from_file(CONFIG_PATH)

    # Excel 路径优先级：命令行参数 > config.yaml > 工程目录自动发现
    excel_path = None
    idx = args.index("--import-org")
    if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
        excel_path = Path(args[idx + 1])
    elif hasattr(cfg, "org_excel_path") and cfg.org_excel_path:
        excel_path = Path(cfg.org_excel_path)
    else:
        # 自动发现工程目录下的 xlsx 文件（取最新修改的）
        candidates = sorted(
            Path(__file__).parent.glob("*.xlsx"),
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        if candidates:
            excel_path = candidates[0]
            print(f"Auto-detected Excel: {excel_path}")

    if not excel_path or not Path(excel_path).exists():
        print(f"ERROR: Excel file not found: {excel_path}")
        print("Usage: python run.py --import-org [path/to/users.xlsx]")
        print("Or set org_excel_path in config/config.yaml")
        sys.exit(1)

    print(f"=== Import Org from Excel: {excel_path} ===")
    importer = OrgImporter(cfg)
    result = importer.run(excel_path)
    print(f"\n=== Done ===")
    print(f"Departments: created={result['dept_created']}  skipped={result['dept_skipped']}  failed={result['dept_failed']}")
    print(f"Users:       created={result['user_ok']}  skipped={result['user_skipped']}  failed={result['user_failed']}")
    sys.exit(0)

# ── Org sync mode ─────────────────────────────────────────────

if "--sync-org" in args:
    print("=== Organization Sync: Departments + Users ===")
    import httpx, time
    token = get_as_token()
    as_headers = {'Authorization': f'Bearer {token}',
                  'Content-Type': 'application/json'}
    bs_token = auth.get_app_token()  # for BISHENG calls
    from app.connectors.bisheng.token_generator import generate_bs_token
    bs_cookie = generate_bs_token()

    # 1. Pull department tree + users from AnyShare
    print("\n--- Pulling AnyShare org tree ---")
    r = httpx.post(f'{AS_BASE}/api/eacp/v1/department/getroots',
                   json={}, headers=as_headers, timeout=15)
    roots = r.json().get('depinfos', [])
    print(f"Root departments: {len(roots)}")

    dept_tree = []
    all_users = {}
    queue = [(d['depid'], d['name'], None, 0) for d in roots]
    total = 0

    while queue:
        depid, name, parent, depth = queue.pop(0)
        # Sub-departments
        r = httpx.post(f'{AS_BASE}/api/eacp/v1/department/getsubdeps',
                       json={'depid': depid}, headers=as_headers, timeout=10)
        subs = r.json().get('depinfos', []) if r.status_code == 200 else []
        # Users
        r2 = httpx.post(f'{AS_BASE}/api/eacp/v1/department/getsubusers',
                        json={'depid': depid}, headers=as_headers, timeout=10)
        users = r2.json().get('userinfos', []) if r2.status_code == 200 else []

        dept_tree.append({'depid': depid, 'name': name,
                          'parent': parent, 'depth': depth})
        for u in users:
            all_users[u['account']] = u['name']
        for s in subs:
            queue.append((s['depid'], s['name'], name, depth + 1))

        total += 1
        if total % 100 == 0:
            print(f"  {total} depts, {len(all_users)} users...")
        time.sleep(0.05)

    print(f"\nTotal: {len(dept_tree)} departments, {len(all_users)} users")

    # 2. Show summary
    print(f"\n--- Department tree ---")
    for node in dept_tree:
        indent = "  " * min(node['depth'], 4)
        marker = "├── " if node['depth'] > 0 else "■ "
        print(f"{indent}{marker}{node['name']}")

    # 3. Create departments in BISHENG
    print(f"\n--- Creating departments in BISHENG ---")
    dept_map = {}
    created_d = 0
    for node in sorted(dept_tree, key=lambda x: x['depth']):
        name = node['name']
        # Check exists
        try:
            r = httpx.get(
                f'{BS_BASE}/api/v1/department/search?keyword={name}&limit=3',
                cookies={'access_token_cookie': bs_cookie}, timeout=10)
            for item in r.json().get('data', {}).get('data', []):
                if item.get('name') == name:
                    dept_map[node['depid']] = item['id']
                    break
        except: pass
        if node['depid'] in dept_map:
            continue
        # Create
        parent_id = dept_map.get(node['parent']) if node['parent'] else None
        try:
            body = {'name': name}
            if parent_id: body['parent_id'] = parent_id
            r = httpx.post(f'{BS_BASE}/api/v1/department', json=body,
                           cookies={'access_token_cookie': bs_cookie}, timeout=10)
            if r.status_code == 200:
                new_id = r.json().get('data', {}).get('id', 0)
                dept_map[node['depid']] = new_id
                created_d += 1
        except: pass
    print(f"Created: {created_d}, skipped: {len(dept_tree)-created_d}")

    # 4. Create users in BISHENG
    print(f"\n--- Creating users in BISHENG ---")
    # Load existing
    bs_existing = set()
    page = 1
    while True:
        r = httpx.get(f'{BS_BASE}/api/v1/user/list?page={page}&page_size=500',
                      cookies={'access_token_cookie': bs_cookie}, timeout=30)
        if r.status_code != 200: break
        items = r.json().get('data', {}).get('data', [])
        if not items: break
        for u in items:
            eid = u.get('external_id', '')
            if eid: bs_existing.add(eid)
        if len(items) < 500: break
        page += 1
    print(f"Existing BISHENG users: {len(bs_existing)}")

    created_u = 0
    for account, name in all_users.items():
        if account in bs_existing:
            continue
        try:
            r = httpx.post(f'{BS_BASE}/api/v1/user',
                json={'user_name': name, 'external_id': account,
                      'password': 'Sync@123456'},
                cookies={'access_token_cookie': bs_cookie}, timeout=10)
            if r.status_code == 200: created_u += 1
        except: pass
    print(f"Created: {created_u}, skipped: {len(all_users)-created_u}")

    print(f"\n=== Org Sync Done ===")
    print(f"Departments: {len(dept_tree)} total, {created_d} new")
    print(f"Users: {len(all_users)} total, {created_u} new")
    sys.exit(0)

# ── Daemon mode ───────────────────────────────────────────────

if daemon_mode:
    console_token = auth.get_user_token(ADMIN_ACCOUNT)
    interval = 3600
    for i, a in enumerate(args):
        if a == "--interval" and i+1 < len(args):
            try: interval = int(args[i+1])
            except: pass

    gns = args[0] if len(args) > 0 else "gns://1A71734693F8464A9B8C1980D4AFBB44"
    space = args[1] if len(args) > 1 else "公司资质_auto"

    from app.sync_pipeline import SyncPipeline
    from app.services.log_scheduler import LogSyncScheduler
    pipeline = SyncPipeline(BS_BASE, "", AS_BASE, get_as_token(), as_auth=auth, as_account=ADMIN_ACCOUNT)
    scheduler = LogSyncScheduler(pipeline, console_token, "", interval)

    print(f"=== Initial full sync ===")
    pipeline.run(gns, space)
    print(f"=== Daemon started (interval={interval}s) ===")
    try:
        scheduler.run_forever()
    except KeyboardInterrupt:
        scheduler.stop()
    sys.exit(0)

# ── Single scope / interactive ────────────────────────────────

if len(args) >= 2:
    gns = args[0]
    space = args[1]
else:
    print("\n=== AnyShare → BISHENG Sync ===")
    print("1. Tree sync (知识库+部门库 from config.yaml)")
    print("2. Personal lib (--user)")
    print("3. Single scope")
    print("4. Batch")
    print("5. List libraries")
    choice = input("\nChoice [1-5]: ").strip()

    if choice == "1":
        os.system(f'python run.py --tree')
    elif choice == "2":
        user = input("Username: ").strip()
        os.system(f'python run.py --user {user}')
    elif choice == "3":
        gns = input("GNS: ").strip()
        space = input("Space name: ").strip()
    elif choice == "4":
        os.system(f'python run.py --batch')
    elif choice == "5":
        os.system(f'python run.py --list knowledge')
    else:
        sys.exit(0)
    if choice in ("1","2","4","5"):
        sys.exit(0)

# Parse flags from remaining args
source_type = "knowledge_doc_lib"
ancestors = None
skip_download = "--skip-download" in args
no_root_perms = "--no-root-perms" in args
grant_owner = None
for i, a in enumerate(args):
    if a == "--type" and i+1 < len(args): source_type = args[i+1]
    if a == "--ancestors" and i+1 < len(args): ancestors = args[i+1].split(",")
    if a == "--grant-owner" and i+1 < len(args): grant_owner = args[i+1]

from app.sync_pipeline import SyncPipeline
pipeline = SyncPipeline(BS_BASE, "", AS_BASE, get_as_token(),
                        as_auth=auth, as_account=ADMIN_ACCOUNT)
result = pipeline.run(gns, space, ancestors=ancestors,
                      skip_download=skip_download, source_type=source_type,
                      incremental=incremental, grant_owner=grant_owner,
                      no_root_perms=no_root_perms)
print(f"\n=== Result ===")
for k, v in result.items(): print(f"  {k}: {v}")
if result.get("space_id"):
    print(f"View: {BS_BASE} → {space} (id={result['space_id']})")
