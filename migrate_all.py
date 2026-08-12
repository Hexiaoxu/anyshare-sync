"""
全量迁移脚本 — 读取 config.yaml 和 data/personal_libs.json，自动迁移所有库

用法:
    python3 migrate_all.py                  # 迁移所有库
    python3 migrate_all.py --knowledge      # 只迁移知识库
    python3 migrate_all.py --dept           # 只迁移部门文档库
    python3 migrate_all.py --personal       # 只迁移个人库
    python3 migrate_all.py --with-files     # 迁移时同步文件内容
"""
import sys, io, json, os, subprocess, time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

from app.config import cfg
from app.connectors.anyshare.auth import AnyShareAuth
from app.connectors.bisheng.token_generator import generate_bs_token

args = sys.argv[1:]
only_knowledge = '--knowledge' in args
only_dept      = '--dept' in args
only_personal  = '--personal' in args
with_files     = '--with-files' in args
run_all        = not any([only_knowledge, only_dept, only_personal])

print('=== 全量迁移开始 ===\n')

auth = AnyShareAuth(cfg.as_base, cfg.as_client_id, cfg.as_client_secret)
bs_token = generate_bs_token()

results = {'ok': [], 'fail': []}

def run_sync(dept_name: str, dept_gns: str, label: str = ''):
    """运行 sync_dept_lib.py 迁移单个库"""
    env = os.environ.copy()
    env['DEPT_NAME'] = dept_name
    env['DEPT_GNS']  = dept_gns
    cmd = [sys.executable, 'sync_dept_lib.py']
    if with_files:
        cmd.append('--with-files')
    print(f'\n{"="*60}')
    print(f'[{label}] {dept_name}')
    print(f'  GNS: {dept_gns}')
    r = subprocess.run(cmd, env=env, encoding='utf-8', errors='replace')
    if r.returncode == 0:
        results['ok'].append(dept_name)
        print(f'  ✅ 成功')
    else:
        results['fail'].append(dept_name)
        print(f'  ❌ 失败 (rc={r.returncode})')
    return r.returncode == 0


# ── 1. 知识库 ─────────────────────────────────────────────────
if run_all or only_knowledge:
    knowledge_items = []
    for tree in cfg.trees:
        if tree.get('type') == 'knowledge_doc_lib':
            knowledge_items.extend(tree.get('items', []))

    if knowledge_items:
        print(f'── 知识库迁移（共 {len(knowledge_items)} 个）──')
        for item in knowledge_items:
            run_sync(item['name'], item['gns'], '知识库')
    else:
        print('[SKIP] config.yaml 中没有知识库配置，请先运行 discover.py')


# ── 2. 部门文档库 ─────────────────────────────────────────────
if run_all or only_dept:
    dept_items = []
    for tree in cfg.trees:
        if tree.get('type') == 'department_doc_lib':
            dept_items.extend(tree.get('items', []))

    if not dept_items:
        print('[SKIP] config.yaml 中没有部门文档库配置，请先运行 discover.py')
    elif cfg.dept_lib_mode == 'single':
        # 整个组织文档库作为一个知识空间
        print(f'\n── 部门文档库迁移（single模式，共 {len(dept_items)} 个根库）──')
        for item in dept_items:
            run_sync(item['name'], item['gns'], '部门库/single')
    elif cfg.dept_lib_mode == 'per_dept':
        # 每个子部门单独一个知识空间
        print(f'\n── 部门文档库迁移（per_dept模式）──')
        print('  扫描子部门列表...')
        from app.connectors.anyshare.auth import AnyShareAuth as _Auth
        from app.connectors.anyshare.scanner import AnyShareScanner
        _auth = _Auth(cfg.as_base, cfg.as_client_id, cfg.as_client_secret)
        _token = _auth.get_user_token(cfg.as_admin_account)
        _headers = {'Authorization': f'Bearer {_token}'}
        import httpx as _hx
        for root_item in dept_items:
            # 列出根库下的直接子文件夹（每个子文件夹=一个部门）
            r = _hx.get(
                f'{cfg.as_base}/api/efast/v1/doc-lib/entry/list',
                params={'gns': root_item['gns'], 'by': 'name', 'sort': 'asc', 'offset': 0, 'limit': 500},
                headers=_headers, timeout=30)
            children = r.json().get('entries', [])
            print(f'  发现 {len(children)} 个子部门')
            for child in children:
                child_name = child.get('name', '')
                child_gns  = child.get('id', '')
                if child_name and child_gns:
                    run_sync(child_name, child_gns, '部门库/per_dept')
    else:
        print(f'[ERROR] 未知的 dept_lib_mode: {cfg.dept_lib_mode}')


# ── 3. 个人库 ─────────────────────────────────────────────────
if run_all or only_personal:
    personal_path = Path('data/personal_libs.json')
    if not personal_path.exists():
        print('[SKIP] data/personal_libs.json 不存在，请先运行 discover.py')
    else:
        with open(personal_path, encoding='utf-8') as f:
            personal_libs = json.load(f)

        print(f'\n── 个人库迁移（共 {len(personal_libs)} 个）──')

        # 读取用户名映射
        users_map = {}
        users_path = Path('users_import.json')
        if users_path.exists():
            with open(users_path, encoding='utf-8') as f:
                users = json.load(f)
            users_map = {u['display']: u['username'] for u in users}

        for lib in personal_libs:
            name = lib['name']
            gns  = lib['gns']
            owner_name = lib.get('owner_name', '')
            username = users_map.get(owner_name, '') or users_map.get(name, '')

            env = os.environ.copy()
            cmd = [sys.executable, 'sync_one_user.py',
                   auth.get_user_token(cfg.as_admin_account),
                   bs_token, gns, name]
            print(f'\n[个人库] {name} (owner={owner_name}, username={username})')
            r = subprocess.run(cmd, env=env, encoding='utf-8', errors='replace')
            if r.returncode == 0:
                results['ok'].append(f'个人库/{name}')
            else:
                results['fail'].append(f'个人库/{name}')


# ── 汇总 ──────────────────────────────────────────────────────
print(f'\n{"="*60}')
print(f'=== 迁移汇总 ===')
print(f'  成功: {len(results["ok"])} 个')
print(f'  失败: {len(results["fail"])} 个')
if results['fail']:
    print('\n失败列表:')
    for name in results['fail']:
        print(f'  - {name}')
