"""批量迁移目标用户个人库到 BISHENG"""

import sys, io, json, subprocess, time, base64, hmac, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

from app.config import cfg
from app.connectors.anyshare.auth import AnyShareAuth

AS_BASE = cfg.as_base
BS_BASE = cfg.bs_base
import sys; sys.path.insert(0, '.')
from app.connectors.bisheng.token_generator import generate_bs_token
BROWSER_TOKEN = generate_bs_token()

auth = AnyShareAuth(AS_BASE, cfg.as_client_id, cfg.as_client_secret)

# 用户名映射
with open('users_import.json', encoding='utf-8') as f:
    all_users = json.load(f)
display_to_username = {u['display']: u['username'] for u in all_users}

# 有内容的目标库（跳过空库和找不到username的）
targets = [
    ('系统测试用户', 'gns://110F8E071F0243AEBDB4DFD59F52D131'),
    ('知识库测试',   'gns://E0AD7B49157A4D85AF4EA5FF32FC34A1'),
    ('陈馨怡',      'gns://019FB4A45EC2437E8265B04097C2E4C1'),
    ('雷皓楠',      'gns://726A9C8ABEAF436ABAE396D0643B285F'),
    ('刘远国',      'gns://793A217A71FB4119B0572CF90954FD55'),
    ('谢秦雅风',    'gns://3B2049F789004B47937A3CA295A465A4'),
    ('薛凯',        'gns://FAAB2ADB06AA4E1394336DEA377E1C94'),
]

print(f'开始迁移 {len(targets)} 个个人库\n')
results = []

for display, gns in targets:
    username = display_to_username.get(display, '')
    if not username:
        print(f'[SKIP] {display}: 找不到 username')
        continue

    print(f'{"="*60}')
    print(f'[{display}] username={username}  gns={gns}')

    try:
        # 获取用户自己的 AS token
        as_token = auth.get_user_token(username)

        # 调用 sync_one_user.py
        ret = subprocess.run(
            [sys.executable, 'sync_one_user.py',
             as_token, BROWSER_TOKEN, gns, display],
            capture_output=False,
            text=True,
            encoding='utf-8',
            timeout=1800  # 30分钟超时
        )
        status = 'OK' if ret.returncode == 0 else f'FAIL(rc={ret.returncode})'
        results.append((display, status))
        print(f'\n[{display}] {status}\n')

    except subprocess.TimeoutExpired:
        results.append((display, 'TIMEOUT'))
        print(f'\n[{display}] TIMEOUT\n')
    except Exception as e:
        results.append((display, f'ERR: {e}'))
        print(f'\n[{display}] ERR: {e}\n')

print('='*60)
print('迁移汇总:')
for name, status in results:
    print(f'  {name:20} {status}')
