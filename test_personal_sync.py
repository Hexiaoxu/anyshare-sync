"""Test personal lib migration for a specific user"""
import sys
from app.connectors.anyshare.auth import AnyShareAuth

USER = sys.argv[1] if len(sys.argv) > 1 else '5jzhoujiajun'
BS_COOKIE = sys.argv[2] if len(sys.argv) > 2 else 'eyJ...'

# 1. Get token for this user
print(f"=== Get token for {USER} ===")
auth = AnyShareAuth(
    'https://5j-zsgl.powerchina.cn',
    '7b98e7b6-f35e-4613-aeed-5b13112b0ff8',
    'Test123.')
token = auth.get_user_token(USER)
print(f"Token: {token[:50]}...")

# 2. Find personal lib GNS
print(f"\n=== Find personal lib for {USER} ===")
import httpx
offset = 0
lib_gns = None
while True:
    r = httpx.get(
        f'https://5j-zsgl.powerchina.cn/api/efast/v1/doc-lib/user?offset={offset}&limit=200',
        headers={'Authorization': f'Bearer {token}'},
        timeout=60)
    if r.status_code != 200:
        print(f"HTTP {r.status_code}")
        break
    for e in r.json().get('entries', []):
        owners = e.get('owned_by', [])
        if owners and owners[0].get('name') == USER:
            lib_gns = e['id']
            print(f"Found: {e['id']}")
            break
    if lib_gns:
        break
    offset += 200
    if len(r.json().get('entries', [])) < 200:
        break

if not lib_gns:
    print(f"NOT FOUND: {USER} has no personal doc lib")
    sys.exit(1)

# 3. Quick scan
print(f"\n=== Quick scan ===")
from urllib.parse import quote
enc = quote(lib_gns, safe='')
r2 = httpx.get(
    f'https://5j-zsgl.powerchina.cn/api/efast/v1/folders/{enc}/sub_objects?limit=10',
    headers={'Authorization': f'Bearer {token}'}, timeout=60)
if r2.status_code == 200:
    d = r2.json()
    dirs = d.get('dirs', [])
    files = d.get('files', [])
    print(f"Root: {len(dirs)} dirs, {len(files)} files")
    for d in dirs[:5]:
        print(f"  DIR: {d['name'][:50]}")
    for f in files[:5]:
        print(f"  FILE: {f['name'][:50]} ({f.get('size', 0)}b)")

    # ACL
    r3 = httpx.post(
        f'https://5j-zsgl.powerchina.cn/api/eacp/v1/perm2/get',
        json={'docid': lib_gns},
        headers={'Authorization': f'Bearer {token}'}, timeout=30)
    if r3.status_code == 200:
        perms = r3.json().get('perminfos', [])
        print(f"ACL: {len(perms)} entries")
        for p in perms[:5]:
            print(f"  {p.get('accessortype')}: {p.get('accessorname','?')[:40]}")

# 4. Run migration
print(f"\n=== To migrate, run: ===")
print(f'python run_sync.py "{token}" "{BS_COOKIE}" "{lib_gns}" "{USER}_个人库" --type user_doc_lib')
