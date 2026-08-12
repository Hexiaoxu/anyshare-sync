"""
部门文档库迁移脚本
- 创建 BISHENG 知识空间
- 按层级创建所有文件夹
- 同步所有文件夹的 ACL 权限
- 文件下载默认关闭（SYNC_FILES=False），可通过参数开启

用法:
  python sync_dept_lib.py                    # 只同步文件夹+权限，不下载文件
  python sync_dept_lib.py --with-files       # 同步文件夹+文件+权限
"""

import sys, json, time, httpx, uuid, shutil
from pathlib import Path
from urllib.parse import quote
sys.path.insert(0, '.')

# ── 配置 ──────────────────────────────────────────────────────
import os as _os
from app.config import cfg

AS_BASE = cfg.as_base
BS_BASE = cfg.bs_base
import sys; sys.path.insert(0, '.')
from app.connectors.bisheng.token_generator import generate_bs_token
BROWSER_TOKEN = generate_bs_token()

# 文件同步开关：False=只同步文件夹结构，True=同时下载上传文件
SYNC_FILES = "--with-files" in sys.argv

# 部门名和 GNS：优先环境变量，其次命令行，最后默认值（测试用）
DEPT_NAME = _os.environ.get("DEPT_NAME", "人力资源部")
DEPT_GNS  = _os.environ.get("DEPT_GNS",
    "gns://0C9379F8E48545FEBE837679F3B4D9FA/11C780161B4D4F7BB9E227D6E332E37B"
    "/26FBA3F5DCAB467D9BB150C19FAFE75E/CB95075F74E34552B2D9577A338EDF87")
AS_ACCOUNT = cfg.as_admin_account

# ── AS Token ──────────────────────────────────────────────────
from app.connectors.anyshare.auth import AnyShareAuth
auth = AnyShareAuth(AS_BASE, cfg.as_client_id, cfg.as_client_secret)
AS_TOKEN = auth.get_user_token(AS_ACCOUNT)
as_headers = {'Authorization': f'Bearer {AS_TOKEN}'}
bs_cookies = {'access_token_cookie': BROWSER_TOKEN}

print(f'=== 部门文档库迁移: {DEPT_NAME} ===')
print(f'文件同步: {"开启" if SYNC_FILES else "关闭（只同步文件夹+权限）"}')
print()


# ── 1. 复用已有空间，或创建新空间 ────────────────────────────
print(f'[1/5] 准备 BISHENG 知识空间...')
r = httpx.get(f'{BS_BASE}/api/v1/knowledge/space/mine', cookies=bs_cookies, timeout=10)
existing = next((sp for sp in r.json().get('data', []) if sp.get('name') == DEPT_NAME), None)

if existing:
    SP_ID = existing['id']
    print(f'  复用已有空间: {DEPT_NAME} (id={SP_ID})')
else:
    r = httpx.post(f'{BS_BASE}/api/v1/knowledge/space',
        json={'name': DEPT_NAME, 'description': f'AnyShare部门文档库 - {DEPT_NAME}', 'auth_type': 'public'},
        cookies=bs_cookies, timeout=10)
    SP_ID = r.json()['data']['id']
    print(f'  创建空间: {DEPT_NAME} (id={SP_ID})')

# 写入 SyncSpaceMapping
try:
    sys.path.insert(0, '.')
    from app.models import init_db, get_session
    from app.models.space_mapping import SyncSpaceMapping
    from sqlmodel import select
    init_db()
    with get_session() as s:
        existing_map = s.exec(select(SyncSpaceMapping).where(
            SyncSpaceMapping.source_doc_lib_id == DEPT_GNS)).first()
        if not existing_map:
            sm = SyncSpaceMapping(
                tenant_id=1,
                source_doc_lib_id=DEPT_GNS,
                source_doc_lib_name=DEPT_NAME,
                source_type='dept_doc_lib',
                target_space_id=SP_ID,
                status='created'
            )
            s.add(sm)
            s.commit()
            print(f'  映射已写入数据库: {DEPT_NAME} -> space_id={SP_ID}')
        else:
            print(f'  映射已存在: {DEPT_NAME} -> space_id={existing_map.target_space_id}')
except Exception as e:
    print(f'  [WARN] 映射写入失败: {e}')


# ── 2. BFS 扫描所有文件夹 ─────────────────────────────────────
print(f'\n[2/5] 扫描 AnyShare 文件夹结构...')
all_dirs  = []   # [{id, name, parent_gns, depth}]
all_files = []   # [{id, name, parent_gns, size}]
queue = [(DEPT_GNS, None, 0)]
scanned = set()

while queue:
    gns, parent_gns, depth = queue.pop(0)
    if gns in scanned:
        continue
    scanned.add(gns)

    enc = quote(gns, safe='')
    try:
        r = httpx.get(
            f'{AS_BASE}/api/efast/v1/folders/{enc}/sub_objects?limit=200&sort=name&direction=asc',
            headers=as_headers, timeout=30)
        if r.status_code != 200:
            print(f'  [WARN] {gns[:50]} -> {r.status_code}')
            continue
        sub = r.json()
    except Exception as e:
        print(f'  [ERR] scan {gns[:50]}: {e}')
        continue

    for d in sub.get('dirs', []):
        all_dirs.append({'id': d['id'], 'name': d['name'],
                         'parent_gns': gns, 'depth': depth + 1})
        queue.append((d['id'], gns, depth + 1))

    for f in sub.get('files', []):
        if not f['name'].lower().endswith(('.zip', '.7z', '.rar', '.tar', '.gz')):
            all_files.append({'id': f['id'], 'name': f['name'],
                               'parent_gns': gns, 'size': f.get('size', 0)})

    if (len(all_dirs) + len(all_files)) % 100 == 0:
        print(f'  扫描中... {len(all_dirs)} 文件夹 / {len(all_files)} 文件', flush=True)

print(f'  完成: {len(all_dirs)} 文件夹 / {len(all_files)} 文件')


# ── 3. 在 BISHENG 创建文件夹结构 ──────────────────────────────
print(f'\n[3/5] 创建 BISHENG 文件夹结构...')
folder_map = {}  # AnyShare GNS -> BISHENG folder_id
created_f = reused_f = failed_f = 0

# 预加载已有文件夹（按 name 索引，用于复用）
def load_bs_children(space_id, parent_id=None):
    """Load existing BISHENG folders for a given parent."""
    params = {'parent_id': parent_id} if parent_id else {}
    r = httpx.get(f'{BS_BASE}/api/v1/knowledge/space/{space_id}/children',
        params=params, cookies=bs_cookies, timeout=15)
    if r.status_code == 200:
        return {item['file_name']: item['id']
                for item in r.json().get('data', {}).get('data', [])
                if item.get('file_type') == 0}  # 0 = folder
    return {}

# 按深度排序（父节点先创建）
for d in sorted(all_dirs, key=lambda x: x['depth']):
    parent_gns = d['parent_gns']
    parent_id  = folder_map.get(parent_gns)  # None = 根

    try:
        r = httpx.post(f'{BS_BASE}/api/v1/knowledge/space/{SP_ID}/folders',
            json={'name': d['name'], 'parent_id': parent_id},
            cookies=bs_cookies, timeout=15)
        resp = r.json()
        if resp.get('status_code') == 200:
            fid = resp['data']['id']
            folder_map[d['id']] = fid
            created_f += 1
            if created_f % 50 == 0:
                print(f'  已创建 {created_f} 个文件夹...', flush=True)
        elif resp.get('status_code') == 18012 or 'already exists' in resp.get('status_message', '').lower():
            # 文件夹已存在 — 查询并复用
            existing = load_bs_children(SP_ID, parent_id)
            fid = existing.get(d['name'])
            if fid:
                folder_map[d['id']] = fid
                reused_f += 1
            else:
                failed_f += 1
                if failed_f <= 3:
                    print(f'  [FAIL] {d["name"]}: {resp.get("status_message","")[:60]}')
        else:
            failed_f += 1
            if failed_f <= 3:
                print(f'  [FAIL] {d["name"]}: {resp.get("status_message","")[:60]}')
    except Exception as e:
        failed_f += 1
        if failed_f <= 3:
            print(f'  [ERR] {d["name"]}: {e}')

print(f'  文件夹: 创建={created_f} 复用={reused_f} 失败={failed_f}')


# ── 4. 文件迁移（受 SYNC_FILES 开关控制）─────────────────────
ok_f = ng_f = skip_f = 0
file_id_map = {}  # AnyShare GNS -> BISHENG file_id

if not SYNC_FILES:
    print(f'\n[4/5] 文件同步已关闭（共 {len(all_files)} 个文件待同步）')
    print(f'  提示: 使用 --with-files 参数开启文件迁移')
    skip_f = len(all_files)
else:
    print(f'\n[4/5] 迁移文件（共 {len(all_files)} 个）...')
    td = Path.home() / 'AppData' / 'Local' / 'Temp' / 'dept_sync' / uuid.uuid4().hex[:8]
    td.mkdir(parents=True, exist_ok=True)

    for i, f in enumerate(all_files):
        nm = f['name']
        print(f'  [{i+1}/{len(all_files)}] {nm[:50]}', end=' ', flush=True)
        try:
            # 下载
            r = httpx.post(f'{AS_BASE}/api/efast/v1/file/osdownload',
                json={'docid': f['id'], 'rev': '', 'authtype': 'QUERY_STRING',
                      'savename': nm, 'usehttps': True},
                headers=as_headers, timeout=30)
            a = r.json()['authrequest']
            hh = {h.split(': ',1)[0]: h.split(': ',1)[1] for h in a[2:] if ': ' in h}
            sf = ''.join(c for c in nm if c.isalnum() or c in '._-()（）')
            lp = td / sf
            with httpx.Client(timeout=120) as cc:
                with cc.stream(a[0], a[1], headers=hh) as rr:
                    rr.raise_for_status()
                    with open(lp, 'wb') as ff:
                        for ch in rr.iter_bytes(65536): ff.write(ch)

            # 上传
            with open(lp, 'rb') as fh:
                r2 = httpx.post(f'{BS_BASE}/api/v1/knowledge/upload/{SP_ID}',
                    files={'file': fh}, cookies=bs_cookies, timeout=120)
            fp = r2.json()['data']['file_path']

            # 注册到文件夹
            pfid = folder_map.get(f['parent_gns'])
            r3 = httpx.post(f'{BS_BASE}/api/v1/knowledge/space/{SP_ID}/files',
                json={'file_path': [fp], 'parent_id': pfid},
                cookies=bs_cookies, timeout=30)
            fid = r3.json()['data'][0]['id']
            file_id_map[f['id']] = fid
            print(f'OK({lp.stat().st_size}B)', flush=True)
            ok_f += 1
            lp.unlink(missing_ok=True)
        except Exception as e:
            print(f'SKIP:{str(e)[:60]}', flush=True)
            ng_f += 1

    shutil.rmtree(td, ignore_errors=True)
    print(f'  文件: 成功={ok_f} 失败={ng_f}')


# ── 5. ACL 权限同步 ──────────────────────────────────────────
print(f'\n[5/5] 同步文件夹权限...')

def translate_relation(allows: set) -> str | None:
    if 'download' not in allows: return None
    if allows >= {'display','preview','download','modify','create','delete','internal_sharing'}:
        return 'manager'
    if allows >= {'display','preview','download','modify','create'}:
        return 'editor'
    return 'viewer'

# 收集所有文件夹的 ACL
acl_items = [(d['name'], d['id'], folder_map.get(d['id']), 'folder')
             for d in all_dirs if folder_map.get(d['id'])]
if SYNC_FILES:
    acl_items += [(f['name'], f['id'], file_id_map.get(f['id']), 'knowledge_file')
                  for f in all_files if file_id_map.get(f['id'])]

# 根目录（知识空间本身）也需要同步权限
acl_items = [(DEPT_NAME, DEPT_GNS, SP_ID, 'knowledge_space')] + acl_items

print(f'  收集 ACL for {len(acl_items)} 项...', flush=True)

# 批量收集 ACL + 解析需要的用户/部门
acl_cache = {}
needed_users = set()
needed_depts = set()

for name, any_gns, bs_id, res_type in acl_items:
    try:
        r = httpx.post(f'{AS_BASE}/api/eacp/v1/perm2/get',
            json={'docid': any_gns}, headers=as_headers, timeout=10)
        if r.status_code == 200:
            perms = r.json().get('perminfos', [])
            acl_cache[any_gns] = perms
            for p in perms:
                atype = p.get('accessortype', 'user')
                aname = p.get('accessorname', '')
                if atype == 'department':
                    needed_depts.add(aname)
                else:
                    if aname: needed_users.add(aname)  # 保留完整格式 "ext_id/**eisoo**/display"
    except: pass

print(f'  {len(acl_cache)} 项有ACL，{len(needed_users)} 用户 + {len(needed_depts)} 部门需解析')

# 解析用户 — 用显示名搜索，再用 external_id 精确匹配
bs_user_map = {}
for name in needed_users:
    try:
        parts   = name.split('/**eisoo**/')
        ext_id  = parts[0]
        display = parts[1] if len(parts) > 1 else parts[0]

        r = httpx.get(
            f'{BS_BASE}/api/v1/permissions/resources/knowledge_space/{SP_ID}/grant-subjects/users',
            params={'keyword': display, 'page': 1, 'page_size': 10},
            cookies=bs_cookies, timeout=10)
        # 优先 external_id 精确匹配，fallback 到 user_name 精确匹配
        matched = next((u for u in r.json().get('data', [])
                        if u.get('external_id') == ext_id), None)
        if not matched:
            matched = next((u for u in r.json().get('data', [])
                            if u.get('user_name') == display), None)
        if matched:
            uid = matched['user_id']
            bs_user_map[display] = (uid, 'user')
            bs_user_map[ext_id]  = (uid, 'user')
    except:
        pass

# 解析部门
bs_dept_map = {}
for dept_name in needed_depts:
    try:
        r = httpx.get(
            f'{BS_BASE}/api/v1/departments/search',
            params={'keyword': dept_name, 'limit': 10},
            cookies=bs_cookies, timeout=10)
        def find_match(nodes, target):
            for n in nodes:
                if n.get('name') == target: return n.get('id')
                r = find_match(n.get('children', []), target)
                if r: return r
            return None
        for root in r.json().get('data', {}).get('roots', []):
            # 先检查 root 本身，再递归检查 children
            did = find_match([root], dept_name)
            if did: bs_dept_map[dept_name] = did; break
    except: pass

print(f'  解析完成: {len(bs_user_map)//2} 用户 / {len(bs_dept_map)} 部门')

# 执行授权
synced = 0
_debug_shown = False
for name, any_gns, bs_id, res_type in acl_items:
    if not bs_id: continue
    perms = acl_cache.get(any_gns, [])
    if not perms: continue

    grants = []
    for p in perms:
        allows = set(p.get('allow', []))
        if set(p.get('deny', [])): continue
        relation = translate_relation(allows)
        if not relation: continue
        atype = p.get('accessortype', 'user')
        aname = p.get('accessorname', '')
        if atype == 'department':
            did = bs_dept_map.get(aname)
            if did:
                # folder 类型跳过根部门 id=1（BISHENG 不支持），knowledge_space 类型允许
                if did == 1 and res_type == 'folder':
                    continue
                grants.append({'subject_type': 'department', 'subject_id': did,
                               'relation': relation})
        else:
            parts = aname.split('/**eisoo**/')
            display = parts[1] if len(parts) > 1 else parts[0]
            found = bs_user_map.get(display) or bs_user_map.get(parts[0])
            if found:
                grants.append({'subject_type': found[1], 'subject_id': found[0],
                               'relation': relation})

    # debug 第一个 item
    if not _debug_shown:
        _debug_shown = True
        print(f'  [debug] first item: name={name} bs_id={bs_id} perms={len(perms)} grants={len(grants)}', flush=True)
        if grants: print(f'  [debug] grants: {grants[:2]}', flush=True)
        else: print(f'  [debug] perms sample: {perms[:2]}', flush=True)

    if not grants: continue
    try:
        r = httpx.post(
            f'{BS_BASE}/api/v1/permissions/resources/{res_type}/{bs_id}/authorize',
            json={'grants': grants, 'revokes': []},
            cookies=bs_cookies, timeout=60)
        sc = r.json().get('status_code')
        if sc == 200:
            synced += 1
        elif not _debug_shown or synced == 0:
            print(f'  [debug] authorize fail: {sc} {r.json().get("status_message","")[:60]}', flush=True)
    except Exception as e:
        print(f'  [debug] authorize err: {e}', flush=True)

print(f'  权限同步: {synced}/{len(acl_items)} 项')

# ── 完成 ─────────────────────────────────────────────────────
print(f'\n=== 完成 ===')
print(f'空间: {DEPT_NAME} (id={SP_ID})')
print(f'文件夹: {created_f} 个')
print(f'文件: {"跳过" if not SYNC_FILES else f"{ok_f}/{len(all_files)}"}')
print(f'权限: {synced}/{len(acl_items)} 项')
print(f'查看: {BS_BASE} → 知识空间 → {DEPT_NAME}')
