"""
批量导入组织架构和用户到 BISHENG（正确版本）

创建用户 API: POST /api/v1/departments/local-members
字段：
  dept_id    = 部门的 BS@xxxxxx 字符串 id
  user_name  = 显示名（中文名）← AnyShare 显示名
  person_id  = 用户名（英文ID）← AnyShare 用户名
  password   = RSA 加密后的密文
  role_ids   = [2]（普通用户）

部门 API:
  GET  /api/v1/departments/children?parent_id=<id>&include_archived=true
  POST /api/v1/departments/  body: {name, parent_id(int)}
"""

import json, time, base64, hmac, hashlib, httpx, sys, io, re, struct
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from cryptography.hazmat.backends import default_backend

import sys; sys.path.insert(0, '.')
from app.config import cfg
from app.connectors.bisheng.token_generator import generate_bs_token

JSON_PATH    = r'users_import.json'
BS_BASE      = cfg.bs_base
ROOT_DEPT_ID = 1
PASSWORD     = 'Sync@123456'

BROWSER_TOKEN = generate_bs_token()


def make_token():
    def b64url(d):
        if isinstance(d, str): d = d.encode()
        return base64.urlsafe_b64encode(d).rstrip(b'=').decode()
    s = 'secret_cF2kD4lW9wY4zL7eX1zX9vS1fA7eW4lQ'
    now = int(time.time())
    sub = json.dumps({'user_id':1,'user_name':'admin','tenant_id':1,'token_version':1})
    h = b64url(json.dumps({'alg':'HS256','typ':'JWT'}, separators=(',',':')))
    p = b64url(json.dumps({'sub':sub,'exp':now+86400,'iss':'bisheng'}, separators=(',',':')))
    sig = hmac.new(s.encode(), f'{h}.{p}'.encode(), hashlib.sha256).digest()
    return f'{h}.{p}.{b64url(sig)}'

def gc():
    return {'access_token_cookie': BROWSER_TOKEN}


# ── RSA 加密 ──────────────────────────────────────────────────
def get_rsa_cipher():
    """获取 RSA 公钥并返回加密函数"""
    r = httpx.get(f'{BS_BASE}/api/v1/user/public_key', cookies=gc(), timeout=10)
    pubkey_pem = r.json()['data']['public_key']
    b64_data = re.sub(r'-----.*?-----|\s', '', pubkey_pem)
    der = base64.b64decode(b64_data)

    def parse_pkcs1_der(der):
        pos = 0
        def read_tlv():
            nonlocal pos
            t = der[pos]; pos += 1
            l = der[pos]; pos += 1
            if l & 0x80:
                nb = l & 0x7f
                l = int.from_bytes(der[pos:pos+nb], 'big'); pos += nb
            v = der[pos:pos+l]; pos += l
            return t, v
        _, seq = read_tlv()
        def read_int(data):
            p = 0
            t = data[p]; p += 1
            l = data[p]; p += 1
            if l & 0x80:
                nb = l & 0x7f
                l = int.from_bytes(data[p:p+nb], 'big'); p += nb
            v = data[p:p+l]; p += l
            return int.from_bytes(v, 'big'), data[p:]
        n, rest = read_int(seq)
        e, _ = read_int(rest)
        return n, e

    n, e = parse_pkcs1_der(der)
    pub = RSAPublicNumbers(e, n).public_key(default_backend())
    return pub

print('获取 RSA 公钥...', flush=True)
rsa_pub = get_rsa_cipher()

def encrypt_password(pwd: str) -> str:
    encrypted = rsa_pub.encrypt(pwd.encode(), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode()

PWD_ENCRYPTED = encrypt_password(PASSWORD)
print(f'密码加密完成', flush=True)


# ── 读取用户数据 ──────────────────────────────────────────────
print('读取用户数据...', flush=True)
with open(JSON_PATH, encoding='utf-8') as f:
    users = json.load(f)
print(f'共 {len(users)} 个用户', flush=True)


# ── 提取部门路径 ──────────────────────────────────────────────
print('解析部门路径...', flush=True)
dept_paths = set()
for u in users:
    dept = u.get('dept', '').split(',')[0].strip()
    parts = [p.strip() for p in dept.split('/') if p.strip()]
    if len(parts) <= 1:
        continue
    parts = parts[1:]  # 跳过根公司名
    path = ''
    for p in parts:
        path = path + '/' + p if path else p
        dept_paths.add(path)

dept_paths_sorted = sorted(dept_paths, key=lambda x: x.count('/'))
print(f'共 {len(dept_paths_sorted)} 个部门节点', flush=True)


# ── 查询已有部门（用 children API）────────────────────────────
dept_map = {}      # path -> int id
dept_id_map = {}   # path -> BS@xxx string
seen_ids = set()

def load_dept_children(parent_int_id: int, parent_path: str = ''):
    """递归加载部门子树"""
    r = httpx.get(f'{BS_BASE}/api/v1/departments/children',
                  params={'parent_id': parent_int_id, 'include_archived': 'true'},
                  cookies=gc(), timeout=30)
    d = r.json()
    if d.get('status_code') != 200:
        return
    children = d.get('data', [])
    if not isinstance(children, list):
        return
    for node in children:
        nid     = node.get('id')
        bs_id   = node.get('dept_id', '')
        nm      = node.get('name', '')
        path    = (parent_path + '/' + nm) if parent_path else nm
        if nid not in seen_ids:
            seen_ids.add(nid)
            dept_map[path]    = nid
            dept_id_map[path] = bs_id
        if node.get('has_children'):
            load_dept_children(nid, path)

print('查询已有部门...', flush=True)
load_dept_children(ROOT_DEPT_ID)
print(f'已有 {len(dept_map)} 个部门', flush=True)


# ── 按层级创建部门 ────────────────────────────────────────────
print(f'\n开始创建部门（共 {len(dept_paths_sorted)} 个）...', flush=True)
created_d = skipped_d = failed_d = 0

for path in dept_paths_sorted:
    if path in dept_map:
        skipped_d += 1
        continue

    parts     = path.split('/')
    name      = parts[-1]
    par_path  = '/'.join(parts[:-1]) if len(parts) > 1 else ''
    parent_id = dept_map.get(par_path, ROOT_DEPT_ID) if par_path else ROOT_DEPT_ID

    r = httpx.post(f'{BS_BASE}/api/v1/departments/',
                   json={'name': name, 'parent_id': parent_id},
                   cookies=gc(), timeout=15)
    resp = r.json()

    if resp.get('status_code') == 200:
        data    = resp['data']
        new_id  = data['id']
        bs_id   = data.get('dept_id', '')
        dept_map[path]    = new_id
        dept_id_map[path] = bs_id
        seen_ids.add(new_id)
        created_d += 1
        if created_d % 200 == 0:
            print(f'  已创建 {created_d} 个部门...', flush=True)
    else:
        msg = str(resp.get('status_message', ''))
        if 'exist' in msg.lower() or 'already' in msg.lower():
            skipped_d += 1
            # 重新加载该层找到 id
            load_dept_children(parent_id, par_path)
            if path in dept_map:
                pass  # 已补充
        else:
            failed_d += 1
            if failed_d <= 5:
                print(f'  FAIL [{path}]: {msg[:80]}', flush=True)

    time.sleep(0.03)

print(f'部门完成: 新建={created_d} 跳过={skipped_d} 失败={failed_d}', flush=True)

# 重新全量加载部门映射（确保 dept_id_map 完整）
print('重新加载部门映射...', flush=True)
dept_map.clear(); dept_id_map.clear(); seen_ids.clear()
load_dept_children(ROOT_DEPT_ID)
print(f'部门映射: {len(dept_map)} 个', flush=True)


# ── 查询已有用户 ──────────────────────────────────────────────
print('\n查询已有用户...', flush=True)
existing_persons = set()  # person_id (external_id)
page = 1
while True:
    r = httpx.get(f'{BS_BASE}/api/v1/user/list',
                  params={'page': page, 'page_size': 500},
                  cookies=gc(), timeout=30)
    data = r.json().get('data', {})
    for u in data.get('data', []):
        pid = u.get('external_id') or u.get('person_id') or ''
        if pid:
            existing_persons.add(pid)
    total = data.get('total', 0)
    if page * 500 >= total:
        break
    page += 1
print(f'已有用户: {len(existing_persons)} 个', flush=True)

to_create = [u for u in users if u['username'] not in existing_persons]
print(f'需创建: {len(to_create)} 个，跳过: {len(users)-len(to_create)} 个\n', flush=True)


# ── 批量创建用户 ──────────────────────────────────────────────
print(f'开始创建用户（共 {len(to_create)} 个）...', flush=True)
ok = ng = skip_dup = 0
errors = []

for i, u in enumerate(to_create):
    if i % 500 == 0 and i > 0:
        print(f'  [{i}/{len(to_create)}] 成功={ok} 重复={skip_dup} 失败={ng}', flush=True)

    # 获取 dept BS@xxx id
    dept = u.get('dept', '').split(',')[0].strip()
    parts = [p.strip() for p in dept.split('/') if p.strip()]
    bs_dept_id = 'BS@root'  # 默认挂根
    if len(parts) > 1:
        sub_path = '/'.join(parts[1:])
        bs_dept_id = dept_id_map.get(sub_path, 'BS@root')

    body = {
        'dept_id':   bs_dept_id,
        'user_name': u['display'],    # 中文显示名
        'person_id': u['username'],   # 英文ID
        'password':  PWD_ENCRYPTED,
        'role_ids':  [2],
    }

    try:
        r = httpx.post(f'{BS_BASE}/api/v1/departments/local-members',
                       json=body, cookies=gc(), timeout=15)
        resp = r.json()
        sc = resp.get('status_code')
        if sc == 200:
            ok += 1
        else:
            msg = str(resp.get('status_message', ''))
            if 'exist' in msg.lower() or 'duplicate' in msg.lower() or '1062' in msg or 'already' in msg.lower():
                skip_dup += 1
            else:
                ng += 1
                if ng <= 10:
                    errors.append(f"  FAIL {u['username']} ({u['display']}): {msg[:80]}")
    except Exception as e:
        ng += 1
        if ng <= 10:
            errors.append(f"  ERR  {u['username']}: {e}")

    if i % 20 == 19:
        time.sleep(0.1)

# ── 结果 ──────────────────────────────────────────────────────
print(f'\n=== 导入完成 ===', flush=True)
print(f'部门: 新建={created_d} 跳过={skipped_d} 失败={failed_d}', flush=True)
print(f'用户: 成功={ok} 重复跳过={skip_dup} 失败={ng}', flush=True)
if errors:
    print('\n失败详情:', flush=True)
    for e in errors:
        print(e, flush=True)
