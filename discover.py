"""
自动发现 AnyShare 所有文档库，更新 config/config.yaml 的 trees 部分

用法:
    python3 discover.py              # 发现并更新 config.yaml
    python3 discover.py --dry-run    # 只打印，不写入
"""
import sys, json, yaml, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

from app.config import cfg
from app.connectors.anyshare.auth import AnyShareAuth
from app.connectors.anyshare.doclib import AnyShareDocLib, DocLibType

CONFIG_PATH = Path('config/config.yaml')
dry_run = '--dry-run' in sys.argv

print('=== AnyShare 文档库自动发现 ===\n')

auth = AnyShareAuth(cfg.as_base, cfg.as_client_id, cfg.as_client_secret)
doclib = AnyShareDocLib(
    base_url=cfg.as_base,
    get_app_token=lambda: auth.get_app_token(),
    get_user_token=lambda account: auth.get_user_token(account),
    admin_account=cfg.as_admin_account,
)

# ── 1. 知识库 ─────────────────────────────────────────────────
print('扫描知识库...')
try:
    knowledge_libs = doclib.list_knowledge()
    print(f'  发现 {len(knowledge_libs)} 个知识库')
    for lib in knowledge_libs:
        print(f'    - {lib.name}  ({lib.id})')
except Exception as e:
    print(f'  [WARN] 知识库扫描失败: {e}')
    knowledge_libs = []

# ── 2. 部门文档库 ─────────────────────────────────────────────
print('\n扫描部门文档库...')
try:
    dept_libs = doclib.list_department()
    print(f'  发现 {len(dept_libs)} 个部门文档库')
    for lib in dept_libs:
        print(f'    - {lib.name}  ({lib.id})')
except Exception as e:
    print(f'  [WARN] 部门文档库扫描失败: {e}')
    dept_libs = []

# ── 3. 个人文档库 ─────────────────────────────────────────────
print('\n扫描个人文档库...')
try:
    personal_libs = doclib.list_personal()
    print(f'  发现 {len(personal_libs)} 个个人文档库')
except Exception as e:
    print(f'  [WARN] 个人文档库扫描失败: {e}')
    personal_libs = []

# ── 4. 构建 trees 配置 ────────────────────────────────────────
trees = []

if knowledge_libs:
    trees.append({
        'space_name': '知识库',
        'type': 'knowledge_doc_lib',
        'no_root_perms': True,
        'items': [{'name': lib.name, 'gns': lib.id} for lib in knowledge_libs]
    })

if dept_libs:
    trees.append({
        'space_name': '部门文档库',
        'type': 'department_doc_lib',
        'no_root_perms': True,
        'skip_download': True,
        'items': [{'name': lib.name, 'gns': lib.id} for lib in dept_libs]
    })

# 个人库单独保存到 personal_libs.json（不放进 trees，由 batch_sync_personal.py 处理）
personal_out = Path('data/personal_libs.json')
personal_out.parent.mkdir(exist_ok=True)
personal_data = [
    {'name': lib.name, 'gns': lib.id,
     'owner_id': lib.owner_id, 'owner_name': lib.owner_name}
    for lib in personal_libs
]

print(f'\n=== 发现汇总 ===')
print(f'  知识库:       {len(knowledge_libs)} 个')
print(f'  部门文档库:   {len(dept_libs)} 个')
print(f'  个人文档库:   {len(personal_libs)} 个')

if dry_run:
    print('\n[dry-run] 不写入文件')
    print('\n将写入 config.yaml trees:')
    print(yaml.dump({'sync': {'trees': trees}}, allow_unicode=True, default_flow_style=False))
    print(f'\n将写入 {personal_out}: {len(personal_data)} 条记录')
else:
    # 更新 config.yaml
    with open(CONFIG_PATH, encoding='utf-8') as f:
        config = yaml.safe_load(f)
    config['sync']['trees'] = trees
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f'\n✅ config.yaml trees 已更新')

    # 保存个人库列表
    with open(personal_out, 'w', encoding='utf-8') as f:
        json.dump(personal_data, f, ensure_ascii=False, indent=2)
    print(f'✅ 个人库列表已保存到 {personal_out}')

print('\n完成！接下来运行 migrate_all.py 开始迁移。')
