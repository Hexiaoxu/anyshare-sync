"""Test App Token (client_credentials) for cross-user personal lib access"""
import httpx, base64
from urllib.parse import quote

AS = 'https://5j-zsgl.powerchina.cn'
CID = '7b98e7b6-f35e-4613-aeed-5b13112b0ff8'
CSEC = 'Test123.'

# 1. Get app token via client_credentials
print("=== 1. Get App Token ===")
auth = base64.b64encode(f'{CID}:{CSEC}'.encode()).decode()
r = httpx.post(
    f'{AS}/oauth2/token',
    data={'grant_type': 'client_credentials', 'scope': 'all'},
    headers={'Authorization': f'Basic {auth}',
             'Content-Type': 'application/x-www-form-urlencoded'},
    timeout=15)
print(f"HTTP {r.status_code}")
body = r.json()
print(body)
token = body.get('access_token', '')

if not token:
    print("FAILED: No token")
    exit(1)

print(f"\nToken: {token[:60]}...")

# 2. Test cross-user personal lib access (5j_lim / 李明_test)
print("\n=== 2. Cross-user Personal Lib (5j_lim) ===")
lib = 'gns://3EF7F0473764412F9CDBB1A90AFE3BD0'
enc = quote(lib, safe='')
r2 = httpx.get(
    f'{AS}/api/efast/v1/folders/{enc}/sub_objects?limit=5',
    headers={'Authorization': f'Bearer {token}'},
    timeout=15)
print(f"HTTP {r2.status_code}")
if r2.status_code == 200:
    d = r2.json()
    dirs = len(d.get('dirs', []))
    files = len(d.get('files', []))
    print(f"  {dirs} dirs, {files} files")
    print("\n>>> SUCCESS: App Token can access other users' personal libs! <<<")
else:
    print(f"  {r2.text[:300]}")

# 3. Test department doc lib file download
print("\n=== 3. Dept Doc Lib File Download ===")
# Use a file GNS from the previous scan
file_gns = (
    'gns://0C9379F8E48545FEBE837679F3B4D9FA/'
    '11C780161B4D4F7BB9E227D6E332E37B/'
    '26FBA3F5DCAB467D9BB150C19FAFE75E/'
    'CB95075F74E34552B2D9577A338EDF87/'
    '6ECCC1D8F4CB4ECDAA58473F6402A5FF/'
    '022420DE8616451B9060335676BD56B0'
)
r3 = httpx.post(
    f'{AS}/api/efast/v1/file/osdownload',
    json={'docid': file_gns, 'rev': '', 'authtype': 'QUERY_STRING',
          'savename': 'test.pdf', 'usehttps': True},
    headers={'Authorization': f'Bearer {token}'},
    timeout=15)
print(f"HTTP {r3.status_code}")
if r3.status_code == 200:
    print("  osdownload OK - Dept file download works!")
elif r3.status_code == 404:
    print("  osdownload 404 - File not found (URL may be wrong)")
else:
    print(f"  {r3.text[:200]}")

# 4. Test Console API
print("\n=== 4. Console API ===")
r4 = httpx.post(
    f'{AS}/console/api/EACPLog/GetPageLog',
    json=[{'ncTGetPageLogParam': {
        'userId': '3e7a9110-3de5-11ef-bb23-de677a88534a',
        'start': 0, 'limit': 1, 'maxLogId': 9223372036854775807,
        'logType': 12, 'levels': [], 'macs': [], 'ips': [],
        'displayNames': [], 'opTypes': [], 'msgs': [], 'exMsgs': [],
        'startDate': 1784476800000000, 'endDate': 1784591999999000
    }}],
    headers={'Authorization': f'Bearer {token}',
             'Content-Type': 'application/json;charset=UTF-8'},
    timeout=15)
print(f"HTTP {r4.status_code}")
if r4.status_code == 200 and r4.json():
    print("  Console API works!")
else:
    print(f"  {r4.text[:200]}")
