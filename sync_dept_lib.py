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

from app.logging_helpers import init_script_logging
logger = init_script_logging("sync_dept_lib")

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
AS_ACCOUNT = _os.environ.get("AS_ACCOUNT", cfg.as_admin_account)

# ── AS Token ──────────────────────────────────────────────────
from app.connectors.anyshare.auth import AnyShareAuth
auth = AnyShareAuth(AS_BASE, cfg.as_client_id, cfg.as_client_secret)
AS_TOKEN = auth.get_user_token(AS_ACCOUNT)
as_headers = {'Authorization': f'Bearer {AS_TOKEN}'}
bs_cookies = {'access_token_cookie': BROWSER_TOKEN}

from app.connectors.bisheng.client import BishengClient
from app.connectors.bisheng.permission import BishengPermission
bs_perm = BishengPermission(BishengClient(BS_BASE, BROWSER_TOKEN))

# 瞬态网络错误重试（AnyShare 偶发 "peer closed connection without sending complete
# message body (incomplete chunked read)"，属网络抖动，重试即可）
_RETRYABLE = (httpx.RemoteProtocolError, httpx.ConnectError,
              httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout)

def _retry(fn, retries=3, delay=2.0, label=''):
    """执行 fn()，遇到瞬态网络错误自动重试。"""
    last = None
    for i in range(retries):
        try:
            return fn()
        except _RETRYABLE as e:
            last = e
            if i < retries - 1:
                print(f'  [RETRY] {label} {type(e).__name__}: {e} (第 {i+2}/{retries} 次)', flush=True)
                time.sleep(delay * (i + 1))
    raise last

print(f'=== 部门文档库迁移: {DEPT_NAME} ===')
print(f'文件同步: {"开启" if SYNC_FILES else "关闭（只同步文件夹+权限）"}')
print()


# ── 1. 复用已有空间，或创建新空间 ────────────────────────────
print(f'[1/5] 准备 BISHENG 知识空间...')
r = _retry(lambda: httpx.get(f'{BS_BASE}/api/v1/knowledge/space/mine', cookies=bs_cookies, timeout=10), label='space/mine')
existing = next((sp for sp in r.json().get('data', []) if sp.get('name') == DEPT_NAME), None)

if existing:
    SP_ID = existing['id']
    print(f'  复用已有空间: {DEPT_NAME} (id={SP_ID})')
else:
    r = _retry(lambda: httpx.post(f'{BS_BASE}/api/v1/knowledge/space',
        json={'name': DEPT_NAME, 'description': f'AnyShare部门文档库 - {DEPT_NAME}', 'auth_type': 'public'},
        cookies=bs_cookies, timeout=10), label='space create')
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
    # AnyShare sub_objects 的 limit 太小会静默截断：OA收文 有 2355 子目录+13 文件，
    # 旧代码 limit=200 只扫到 200 个，其余 2000+ 全漏了。
    # next_marker 翻页实测不可靠（marker 含 '+'，URL 编码后服务端不认、翻不动），
    # 而 limit 上限约 9000（10000 会返回空）。故用 limit=5000 一次取全（含文件）。
    try:
        r = _retry(lambda: httpx.get(
            f'{AS_BASE}/api/efast/v1/folders/{enc}/sub_objects?limit=5000&sort=name&direction=asc',
            headers=as_headers, timeout=30), label='sub_objects')
        if r.status_code != 200:
            print(f'  [WARN] {gns[:50]} -> {r.status_code}')
            continue
        sub = r.json()
    except Exception as e:
        print(f'  [ERR] scan {gns[:50]}: {e}')
        continue

    if sub.get('next_marker'):
        print(f'  [WARN] {gns[:50]} 子项超过 5000，仍可能被截断')

    for d in sub.get('dirs', []):
        all_dirs.append({'id': d['id'], 'name': d['name'],
                         'parent_gns': gns, 'depth': depth + 1})
        queue.append((d['id'], gns, depth + 1))

    for f in sub.get('files', []):
        if not f['name'].lower().endswith(('.zip', '.7z', '.rar', '.tar', '.gz')):
            all_files.append({'id': f['id'], 'name': f['name'],
                               'parent_gns': gns, 'size': f.get('size', 0)})

    if (len(all_dirs) + len(all_files)) % 500 == 0:
        print(f'  扫描中... {len(all_dirs)} 文件夹 / {len(all_files)} 文件', flush=True)

print(f'  完成: {len(all_dirs)} 文件夹 / {len(all_files)} 文件')


# ── 3. 在 BISHENG 创建文件夹结构 ──────────────────────────────
print(f'\n[3/5] 创建 BISHENG 文件夹结构...')
folder_map = {}  # AnyShare GNS -> BISHENG folder_id
created_f = reused_f = failed_f = 0

def load_bs_children(space_id, parent_id=None):
    """返回某个父目录下已存在的文件夹 {name: folder_id}。

    注意：BISHENG children API 默认 page_size=20（不传时只返回第一页 20 条），
    必须显式传 page_size。实测 cursor 翻页在 page_size=100 时有重叠漏项 bug
    （OA系统 200 个子目录用 100 翻页只拿回 196 个，漏 4 个 → 判「已存在」却复用
    不到而失败）。因此用足够大的 page_size（500）一次拿全，避免触发有问题的
    cursor 翻页；本迁移单目录最多 200 个子目录，500 足够。仍保留 cursor 作为
    >500 子目录时的兜底（并打印告警）。
    """
    result = {}
    cursor = None
    while True:
        params = {'page_size': 500}
        if parent_id:
            params['parent_id'] = parent_id
        if cursor:
            params['cursor'] = cursor
        r = httpx.get(f'{BS_BASE}/api/v1/knowledge/space/{space_id}/children',
            params=params, cookies=bs_cookies, timeout=20)
        if r.status_code != 200:
            break
        data = r.json().get('data', {})
        items = data.get('data', [])
        if not isinstance(items, list) or not items:
            break
        for item in items:
            if item.get('file_type') == 0:  # 0 = folder
                result[item['file_name']] = item['id']
        if not data.get('has_more'):
            break
        cursor = data.get('next_cursor')
        if not cursor:
            break
        print(f'  [WARN] 目录子项超过 500，触发 cursor 翻页（可能漏项）: parent_id={parent_id}',
              flush=True)
    return result

# 一次性递归构建整个空间已有文件夹的索引 {(parent_id, name): folder_id}，
# 供后续按 (父目录, 名称) 精确复用。相比「先 create 撞 already exists 再查」，
# 避免了父目录复用失败导致子目录级联失败的问题。
def build_folder_index(space_id):
    index = {}
    queue = [None]  # None = 根
    while queue:
        pid = queue.pop(0)
        for name, fid in load_bs_children(space_id, pid).items():
            index[(pid, name)] = fid
            queue.append(fid)
    return index

folder_index = build_folder_index(SP_ID)
print(f'  已有 {len(folder_index)} 个文件夹可复用')

# 按深度排序（父节点先处理），先查索引复用，查不到再创建
for d in sorted(all_dirs, key=lambda x: x['depth']):
    parent_gns = d['parent_gns']
    parent_id  = folder_map.get(parent_gns)  # None = 根

    fid = folder_index.get((parent_id, d['name']))
    if fid:
        folder_map[d['id']] = fid
        reused_f += 1
        continue

    try:
        r = httpx.post(f'{BS_BASE}/api/v1/knowledge/space/{SP_ID}/folders',
            json={'name': d['name'], 'parent_id': parent_id},
            cookies=bs_cookies, timeout=15)
        resp = r.json()
        if resp.get('status_code') == 200:
            fid = resp['data']['id']
            folder_map[d['id']] = fid
            folder_index[(parent_id, d['name'])] = fid
            created_f += 1
            if created_f % 50 == 0:
                print(f'  已创建 {created_f} 个文件夹...', flush=True)
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
        r = _retry(lambda: httpx.post(f'{AS_BASE}/api/eacp/v1/perm2/get',
            json={'docid': any_gns}, headers=as_headers, timeout=10), label='perm2/get')
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
        ok = bs_perm.sync_grants(res_type, bs_id, grants, timeout=60, retries=2)
        if ok:
            synced += 1
        else:
            print(f'  [debug] authorize fail: {res_type}/{bs_id}', flush=True)
    except Exception as e:
        print(f'  [debug] authorize err: {e}', flush=True)

print(f'  权限同步: {synced}/{len(acl_items)} 项')

# ── 完成 ─────────────────────────────────────────────────────
print(f'\n=== 完成 ===')
print(f'空间: {DEPT_NAME} (id={SP_ID})')
print(f'文件夹: {created_f + reused_f} 个（新建 {created_f} / 复用 {reused_f}）')
print(f'文件: {"跳过" if not SYNC_FILES else f"{ok_f}/{len(all_files)}"}')
print(f'权限: {synced}/{len(acl_items)} 项')
print(f'查看: {BS_BASE} → 知识空间 → {DEPT_NAME}')
