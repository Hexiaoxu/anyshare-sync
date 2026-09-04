"""
生成 users_import.json（中文名→英文账号映射）

两种来源（默认优先 Excel，快；无 Excel 时从 AnyShare 组织接口拉取，慢）：
    python generate_users_import.py                         # 自动：优先 Excel，否则 API
    python generate_users_import.py --excel 用户的信息.xlsx  # 从 AnyShare 导出的 Excel 生成（几秒）
    python generate_users_import.py --from-api              # 从 AnyShare 组织接口拉取（约20-30分钟）

生成的 users_import.json 结构：
    [{"username": 英文账号, "display": 中文名, "dept": 部门路径}, ...]
供个人库迁移（batch_sync_personal.py / migrate_all.py）做 display→username 映射。
"""
import sys, io, json, httpx
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

from app.logging_helpers import init_script_logging
logger = init_script_logging("generate_users_import")

from app.config import cfg

OUT = Path('users_import.json')
args = sys.argv[1:]


def from_excel(excel_path):
    """从 AnyShare 导出的 Excel 生成（复用 OrgImporter 的解析逻辑）。"""
    from app.services.org_importer import OrgImporter
    users = OrgImporter.read_excel(excel_path)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    print(f'✅ 从 Excel 生成 {OUT}：{len(users)} 个用户')
    return len(users)


def from_api():
    """从 AnyShare 组织接口拉取（无 Excel 时的兜底，逐部门拉，慢）。"""
    from app.connectors.anyshare.auth import AnyShareAuth

    AS_BASE = cfg.as_base
    auth = AnyShareAuth(AS_BASE, cfg.as_client_id, cfg.as_client_secret)
    token = auth.get_user_token(cfg.as_admin_account)
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    print('=== 从 AnyShare 拉取组织架构，生成 users_import.json ===\n')
    r = httpx.post(f'{AS_BASE}/api/eacp/v1/department/getroots',
                   json={}, headers=headers, timeout=30)
    roots = r.json().get('depinfos', [])
    print(f'根部门: {len(roots)} 个')

    dept_path = {}   # depid -> (name, parent_depid)
    users = []       # {username, display, depid}
    queue = [(d['depid'], d['name'], None) for d in roots]
    seen_dept = set()
    total = 0

    while queue:
        depid, name, parent = queue.pop(0)
        if depid in seen_dept:
            continue
        seen_dept.add(depid)
        dept_path[depid] = (name, parent)

        try:
            r = httpx.post(f'{AS_BASE}/api/eacp/v1/department/getsubdeps',
                           json={'depid': depid}, headers=headers, timeout=30)
            for s in r.json().get('depinfos', []):
                queue.append((s['depid'], s['name'], depid))
        except Exception:
            pass

        try:
            r2 = httpx.post(f'{AS_BASE}/api/eacp/v1/department/getsubusers',
                            json={'depid': depid}, headers=headers, timeout=30)
            for u in r2.json().get('userinfos', []):
                users.append({'username': u['account'],
                              'display': u['name'],
                              'depid': depid})
        except Exception:
            pass

        total += 1
        if total % 200 == 0:
            print(f'  已扫描 {total} 个部门，{len(users)} 个用户...', flush=True)

    print(f'部门: {len(dept_path)} 个，用户记录: {len(users)} 条')

    def full_path(depid):
        parts = []
        d = depid
        while d and d in dept_path:
            nm, parent = dept_path[d]
            parts.append(nm)
            d = parent
        return '/'.join(reversed(parts))

    records = []
    seen = set()
    for u in users:
        if u['username'] in seen:
            continue
        seen.add(u['username'])
        records.append({'username': u['username'],
                        'display': u['display'],
                        'dept': full_path(u['depid'])})

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 从 API 生成 {OUT}：{len(records)} 个用户')
    return len(records)


# ── 主逻辑 ────────────────────────────────────────────────────
if '--from-api' in args:
    from_api()
else:
    # 找 Excel：--excel 参数 > org_excel_path 配置 > 自动发现最新 .xlsx
    excel_path = None
    if '--excel' in args:
        i = args.index('--excel')
        if i + 1 < len(args):
            excel_path = Path(args[i + 1])
    elif getattr(cfg, 'org_excel_path', ''):
        excel_path = Path(cfg.org_excel_path)
    else:
        candidates = sorted(Path('.').glob('*.xlsx'),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            excel_path = candidates[0]

    if excel_path and excel_path.exists():
        from_excel(excel_path)
    else:
        print('[WARN] 未找到 Excel，改用 AnyShare 组织接口拉取（较慢）...')
        from_api()
