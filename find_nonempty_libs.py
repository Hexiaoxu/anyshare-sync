"""Find all personal doc libs that have actual files/directories"""
import httpx
from urllib.parse import quote
from app.connectors.anyshare.auth import AnyShareAuth

auth = AnyShareAuth(
    'https://5j-zsgl.powerchina.cn',
    '7b98e7b6-f35e-4613-aeed-5b13112b0ff8', 'Test123.')
token = auth.get_app_token()
AS = 'https://5j-zsgl.powerchina.cn'

print("=== Fetching personal lib list ===")
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
        if owners:
            libs.append({'user': owners[0]['name'], 'gns': e['id']})
    offset += len(entries)
    print(f"  {offset} libs found...", end='\r')
    if len(entries) < 200:
        break
print(f"\nTotal: {len(libs)} personal libs")

# Check each for content
print("\n=== Checking for content ===")
non_empty = []
checked = 0
for lib in libs:
    checked += 1
    try:
        enc = quote(lib['gns'], safe='')
        r = httpx.get(
            f'{AS}/api/efast/v1/folders/{enc}/sub_objects?limit=3',
            headers={'Authorization': f'Bearer {token}'}, timeout=10)
        if r.status_code == 200:
            d = r.json()
            dirs = len(d.get('dirs', []))
            files = len(d.get('files', []))
            if dirs + files > 0:
                non_empty.append({**lib, 'dirs': dirs, 'files': files})
                print(f"  [{checked}/{len(libs)}] {lib['user']}: {dirs}D/{files}F")
    except:
        pass
    if checked % 100 == 0:
        print(f"  [{checked}/{len(libs)}] scanning... ({len(non_empty)} non-empty)")

print(f"\n=== Results ===")
print(f"Non-empty: {len(non_empty)}")
for lib in non_empty:
    print(f"  {lib['user']:20s}  {lib['dirs']:>3}D/{lib['files']:>3}F  {lib['gns']}")
