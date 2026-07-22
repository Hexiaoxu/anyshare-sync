"""Get user's own token and find their personal lib by name match"""
import sys, httpx
from urllib.parse import quote
from app.connectors.anyshare.auth import AnyShareAuth

USER = sys.argv[1] if len(sys.argv) > 1 else '5jzhoujiajun'

auth = AnyShareAuth(
    'https://5j-zsgl.powerchina.cn',
    '7b98e7b6-f35e-4613-aeed-5b13112b0ff8', 'Test123.')
token = auth.get_user_token(USER)
AS = 'https://5j-zsgl.powerchina.cn'

print(f"User: {USER}")
print(f"Token: {token[:50]}...")

# Search doc-lib/user list for this user by various name formats
print("\n=== Searching ===")
offset = 0
found = None
search_names = [USER, '周佳骏']
while True:
    r = httpx.get(
        f'{AS}/api/efast/v1/doc-lib/user?offset={offset}&limit=200',
        headers={'Authorization': f'Bearer {token}'}, timeout=60)
    if r.status_code != 200:
        print(f"HTTP {r.status_code} at offset {offset}")
        break
    entries = r.json().get('entries', [])
    if not entries:
        break
    for e in entries:
        owners = e.get('owned_by', [])
        if owners:
            oname = owners[0].get('name', '')
            oid = owners[0].get('id', '')
            for sn in search_names:
                if sn.lower() in oname.lower() or sn.lower() in oid.lower():
                    found = e
                    print(f"MATCH: owner={oname}, id={oid}")
                    print(f"GNS: {e['id']}")
                    break
        if found:
            break
    if found:
        break
    offset += len(entries)
    if len(entries) < 200:
        break

if not found:
    # Try: the first entry might be the user's own lib
    print("\nNot found in list. Trying first entry as own lib...")
    r = httpx.get(
        f'{AS}/api/efast/v1/doc-lib/user?offset=0&limit=1',
        headers={'Authorization': f'Bearer {token}'}, timeout=15)
    entries = r.json().get('entries', [])
    if entries:
        e = entries[0]
        print(f"First entry: {e['id']}")
        print(f"Owners: {e.get('owned_by', [])}")
    else:
        print("No entries at all")

if found:
    lib = found['id']
    print(f"\n=== Contents ===")
    enc = quote(lib, safe='')
    r2 = httpx.get(
        f'{AS}/api/efast/v1/folders/{enc}/sub_objects?limit=50',
        headers={'Authorization': f'Bearer {token}'}, timeout=15)
    d = r2.json()
    dirs = d.get('dirs', [])
    files = d.get('files', [])
    print(f"{len(dirs)} dirs, {len(files)} files")
    for dd in dirs[:10]:
        print(f"  DIR: {dd['name'][:60]}")
    for ff in files[:10]:
        print(f"  FILE: {ff['name'][:60]} ({ff.get('size', 0)}b)")

    # ACL
    print(f"\n=== ACL ===")
    r3 = httpx.post(
        f'{AS}/api/eacp/v1/perm2/get',
        json={'docid': lib},
        headers={'Authorization': f'Bearer {token}'}, timeout=10)
    if r3.status_code == 200:
        perms = r3.json().get('perminfos', [])
        print(f"{len(perms)} entries")
    else:
        print(f"HTTP {r3.status_code}")

    # Migration command
    BS = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ7XCJ1c2VyX2lkXCI6IDEsIFwidXNlcl9uYW1lXCI6IFwiYWRtaW5cIiwgXCJ0ZW5hbnRfaWRcIjogMSwgXCJ0b2tlbl92ZXJzaW9uXCI6IDF9IiwiZXhwIjoxNzg0NTk5Mjc2LCJpc3MiOiJiaXNoZW5nIn0.EDbuU7W_Lehk8bUXtGbPm7OmcXqOB1a9WnMvmFAXP1I"
    print(f"\n=== Migrate ===")
    print(f'python run_sync.py "{token}" "{BS}" "{lib}" "{USER}_个人库" --type user_doc_lib')
