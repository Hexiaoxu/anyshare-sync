"""List all personal document libraries using App Token"""
import httpx
from app.connectors.anyshare.auth import AnyShareAuth

# Get app token
auth = AnyShareAuth(
    'https://5j-zsgl.powerchina.cn',
    '7b98e7b6-f35e-4613-aeed-5b13112b0ff8',
    'Test123.')
token = auth.get_app_token()
print(f"App Token: {token[:50]}...")

# Pull all personal doc libs
AS = 'https://5j-zsgl.powerchina.cn'
offset = 0
total = 0
with_files = []
print(f"\n{'用户名':20s}  {'文档库名':25s}  GNS")
print("-" * 80)

while True:
    r = httpx.get(
        f'{AS}/api/efast/v1/doc-lib/user?offset={offset}&limit=200',
        headers={'Authorization': f'Bearer {token}'},
        timeout=60)
    if r.status_code != 200:
        print(f"HTTP {r.status_code} at offset {offset}")
        break
    entries = r.json().get('entries', [])
    if not entries:
        break
    for e in entries:
        owners = e.get('owned_by', [])
        owner_name = owners[0]['name'] if owners else '?'
        lib_name = e.get('name', '')
        lib_gns = e['id']
        print(f"{owner_name:20s}  {lib_name[:25]:25s}  {lib_gns}")
        with_files.append({
            'user': owner_name,
            'name': lib_name,
            'gns': lib_gns,
        })
        total += 1
    offset += len(entries)
    if len(entries) < 200:
        break

print(f"\nTotal: {total} personal doc libs")

# Also show users with no personal lib
if total < 100:
    print("(some users may not have personal doc libs)")
