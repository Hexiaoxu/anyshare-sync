"""Check which personal doc libs have content (files/dirs)"""
import httpx
from urllib.parse import quote
from app.connectors.anyshare.auth import AnyShareAuth

auth = AnyShareAuth(
    'https://5j-zsgl.powerchina.cn',
    '7b98e7b6-f35e-4613-aeed-5b13112b0ff8',
    'Test123.')
token = auth.get_app_token()
AS = 'https://5j-zsgl.powerchina.cn'

# Get all personal libs first
print("=== Fetching all personal doc libs ===")
libs = []
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
        owner = owners[0]['name'] if owners else '?'
        libs.append({'user': owner, 'gns': e['id']})
    offset += len(entries)
    if len(entries) < 200:
        break
print(f"Total: {len(libs)} personal libs")

# Check each for content
print(f"\n{'用户名':20s}  {'目录':>5}  {'文件':>5}  {'ACL':>4}")
print("-" * 45)
non_empty = []
for lib in libs:
    enc = quote(lib['gns'], safe='')
    r = httpx.get(
        f'{AS}/api/efast/v1/folders/{enc}/sub_objects?limit=5',
        headers={'Authorization': f'Bearer {token}'}, timeout=15)
    if r.status_code == 200:
        d = r.json()
        dirs = len(d.get('dirs', []))
        files = len(d.get('files', []))
        # ACL
        acl_count = 0
        try:
            r2 = httpx.post(
                f'{AS}/api/eacp/v1/perm2/get',
                json={'docid': lib['gns']},
                headers={'Authorization': f'Bearer {token}'}, timeout=10)
            if r2.status_code == 200:
                acl_count = len(r2.json().get('perminfos', []))
        except:
            pass
        if dirs + files > 0:
            print(f"{lib['user']:20s}  {dirs:>5}  {files:>5}  {acl_count:>4}")
            non_empty.append(lib)
    else:
        pass  # skip 404/403

print(f"\nNon-empty: {len(non_empty)}")

# Print migration commands for non-empty
if non_empty:
    print("\n=== Migration commands ===")
    for lib in non_empty:
        print(f"# {lib['user']}")
        print(f"python run_sync.py \"$TOKEN\" \"$BS_COOKIE\" \"{lib['gns']}\" "
              f"\"{lib['user']}_个人库\" --type user_doc_lib")
        print()
