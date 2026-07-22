"""Get token for 5j_lim via authentication API + test personal lib"""
import httpx, base64
from urllib.parse import quote

AS = 'https://5j-zsgl.powerchina.cn'
CID = '7b98e7b6-f35e-4613-aeed-5b13112b0ff8'
CSEC = 'Test123.'
USER = '5j_lim'
LIB = 'gns://3EF7F0473764412F9CDBB1A90AFE3BD0'

auth = base64.b64encode(f'{CID}:{CSEC}'.encode()).decode()

# Step 1: Get token for 5j_lim
print("=== Get token for 5j_lim ===")
r = httpx.post(
    f'{AS}/api/authentication/v1/access_token',
    json={'account': USER},
    headers={'Authorization': f'Basic {auth}'},
    timeout=15)
print(f"HTTP {r.status_code}")
print(r.json())
token = r.json().get('access_token', '')

if not token:
    print("FAILED: No token")
    exit(1)

# Step 2: Access personal lib
print("\n=== Access 5j_lim personal lib ===")
enc = quote(LIB, safe='')
r2 = httpx.get(
    f'{AS}/api/efast/v1/folders/{enc}/sub_objects?limit=5',
    headers={'Authorization': f'Bearer {token}'},
    timeout=15)
print(f"HTTP {r2.status_code}")
if r2.status_code == 200:
    d = r2.json()
    print(f"  {len(d.get('dirs', []))} dirs, {len(d.get('files', []))} files")
    for f in d.get('files', [])[:3]:
        print(f"    {f['name'][:50]}")
    print("\n>>> SUCCESS! Programmatic personal lib access works! <<<")
else:
    print(f"  {r2.text[:300]}")

# Step 3: Test download a file from personal lib
print("\n=== Test download from personal lib ===")
r3 = httpx.get(
    f'{AS}/api/efast/v1/folders/{enc}/sub_objects?limit=5',
    headers={'Authorization': f'Bearer {token}'},
    timeout=15)
files = r3.json().get('files', [])
if files:
    f = files[0]
    r4 = httpx.post(
        f'{AS}/api/efast/v1/file/osdownload',
        json={'docid': f['id'], 'rev': '', 'authtype': 'QUERY_STRING',
              'savename': f['name'], 'usehttps': True},
        headers={'Authorization': f'Bearer {token}'},
        timeout=15)
    print(f"osdownload: HTTP {r4.status_code}")
    if r4.status_code == 200:
        print("  Download works!")
    else:
        print(f"  {r4.text[:200]}")
