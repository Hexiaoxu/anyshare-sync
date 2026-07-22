"""Batch migrate specific users' personal doc libs"""
import sys
import httpx
from urllib.parse import quote
from app.connectors.anyshare.auth import AnyShareAuth

# Users to migrate
TARGETS = [
    "5j_test",
    "周佳骏",
    "罗运军",
    "刘宏宇",
    "系统管理用户",
    "能巍",
    "王生瓒",
    "程博",
    "刘远国",
    "雷皓楠",
]

BS_COOKIE = sys.argv[1] if len(sys.argv) > 1 else "eyJ..."

auth = AnyShareAuth(
    'https://5j-zsgl.powerchina.cn',
    '7b98e7b6-f35e-4613-aeed-5b13112b0ff8',
    'Test123.')
token = auth.get_app_token()
AS = 'https://5j-zsgl.powerchina.cn'

# 1. Find all personal libs
print("=== Finding personal libs ===")
all_libs = {}
offset = 0
while True:
    r = httpx.get(
        f'{AS}/api/efast/v1/doc-lib/user?offset={offset}&limit=200',
        headers={'Authorization': f'Bearer {token}'}, timeout=60)
    if r.status_code != 200:
        break
    entries = r.json().get('entries', [])
    if not entries:
        break
    for e in entries:
        owners = e.get('owned_by', [])
        if owners:
            all_libs[owners[0]['name']] = e['id']
    offset += len(entries)
    if len(entries) < 200:
        break
print(f"Found {len(all_libs)} personal libs")

# 2. Match targets and generate commands
print(f"\n=== Migration commands ===\n")
found = 0
for name in TARGETS:
    gns = all_libs.get(name)
    if not gns:
        print(f"# {name}: NOT FOUND (no personal lib)")
        continue
    found += 1

    # Get user token
    try:
        user_token = auth.get_user_token(name)
    except Exception as e:
        print(f"# {name}: Token failed ({e}), skipping")
        print()
        continue

    print(f"# {name} (gns={gns})")
    print(f'python run_sync.py "{user_token}" "{BS_COOKIE}" "{gns}" "{name}_个人库" --type user_doc_lib')
    print()

print(f"Ready: {found}/{len(TARGETS)}")
