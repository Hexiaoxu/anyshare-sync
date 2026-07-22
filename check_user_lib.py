"""Quick check: find a user's personal lib and show contents"""
import sys, httpx
from urllib.parse import quote
from app.connectors.anyshare.auth import AnyShareAuth

USER = sys.argv[1] if len(sys.argv) > 1 else '5jzhoujiajun'

auth = AnyShareAuth(
    'https://5j-zsgl.powerchina.cn',
    '7b98e7b6-f35e-4613-aeed-5b13112b0ff8', 'Test123.')
token = auth.get_user_token(USER)
print(f"Token: {token[:50]}...")
AS = 'https://5j-zsgl.powerchina.cn'

# Find personal lib
print(f"\n=== Finding personal lib for {USER} ===")
offset = 0
lib_gns = None
while True:
    r = httpx.get(
        f'{AS}/api/efast/v1/doc-lib/user?offset={offset}&limit=200',
        headers={'Authorization': f'Bearer {token}'}, timeout=30)
    if r.status_code != 200:
        break
    for e in r.json().get('entries', []):
        owners = e.get('owned_by', [])
        if owners and owners[0].get('name') == USER:
            lib_gns = e['id']
            break
    if lib_gns:
        break
    offset += 200
    if len(r.json().get('entries', [])) < 200:
        break

if not lib_gns:
    print(f"NOT FOUND: {USER}")
    sys.exit(1)
print(f"GNS: {lib_gns}")

# Show contents (depth 1)
print(f"\n=== Contents ===")
enc = quote(lib_gns, safe='')
r = httpx.get(
    f'{AS}/api/efast/v1/folders/{enc}/sub_objects?limit=50',
    headers={'Authorization': f'Bearer {token}'}, timeout=15)
d = r.json()
dirs = d.get('dirs', [])
files = d.get('files', [])
print(f"{len(dirs)} dirs, {len(files)} files")
for dd in dirs[:10]:
    print(f"  DIR: {dd['name'][:60]}")
for ff in files[:10]:
    print(f"  FILE: {ff['name'][:60]} ({ff.get('size', 0)}b)")

# ACL
print(f"\n=== ACL ===")
r2 = httpx.post(
    f'{AS}/api/eacp/v1/perm2/get',
    json={'docid': lib_gns},
    headers={'Authorization': f'Bearer {token}'}, timeout=10)
if r2.status_code == 200:
    perms = r2.json().get('perminfos', [])
    print(f"{len(perms)} entries")
    for p in perms[:5]:
        print(f"  {p.get('accessortype')}: {p.get('accessorname','?')[:50]} allow={p.get('allow')}")

# Migration command
BS = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ7XCJ1c2VyX2lkXCI6IDEsIFwidXNlcl9uYW1lXCI6IFwiYWRtaW5cIiwgXCJ0ZW5hbnRfaWRcIjogMSwgXCJ0b2tlbl92ZXJzaW9uXCI6IDF9IiwiZXhwIjoxNzg0NTk5Mjc2LCJpc3MiOiJiaXNoZW5nIn0.EDbuU7W_Lehk8bUXtGbPm7OmcXqOB1a9WnMvmFAXP1I"
print(f"\n=== To migrate ===")
print(f'python run_sync.py "{token}" "{BS}" "{lib_gns}" "{USER}_个人库" --type user_doc_lib')
