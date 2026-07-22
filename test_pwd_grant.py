"""Test OAuth2 password grant + personal lib access"""
import httpx
from urllib.parse import quote

AS = 'https://5j-zsgl.powerchina.cn'
CID = '994aeb58-ab01-438b-ad32-367eda85779e'
USER = '7b98e7b6-f35e-4613-aeed-5b13112b0ff8'
PW = 'Test123.'

# 1. Try password grant
print("=== OAuth2 password grant ===")
r = httpx.post(
    f'{AS}/oauth2/token',
    data={'grant_type': 'password', 'username': USER, 'password': PW,
          'client_id': CID, 'scope': 'all'},
    headers={'Content-Type': 'application/x-www-form-urlencoded'},
    timeout=15)
print(f"HTTP {r.status_code}")
print(r.json())

token = r.json().get('access_token', '')
if not token:
    # 2. Try authentication API
    print("\n=== Fallback: authentication API ===")
    r2 = httpx.post(
        f'{AS}/api/authentication/v1/access_token',
        json={'account': USER, 'password': PW},
        timeout=15)
    print(f"HTTP {r2.status_code}")
    print(r2.text[:300])
    token = r2.json().get('access_token', '')

if token:
    print(f"\nToken obtained: {token[:60]}...")

    # 3. Find this user's personal lib
    print("\n=== Find personal lib ===")
    offset = 0
    lib_gns = None
    while offset < 9000:
        r3 = httpx.get(
            f'{AS}/api/efast/v1/doc-lib/user?offset={offset}&limit=200',
            headers={'Authorization': f'Bearer {token}'}, timeout=30)
        if r3.status_code != 200:
            break
        for e in r3.json().get('entries', []):
            owners = e.get('owned_by', [])
            if owners and owners[0].get('id') == USER:
                lib_gns = e['id']
                print(f"Found: {owners[0]['name']} -> {lib_gns}")
                break
        if lib_gns:
            break
        offset += 200
        if len(r3.json().get('entries', [])) < 200:
            break

    # 4. Access personal lib
    if lib_gns:
        print(f"\n=== Access personal lib ===")
        enc = quote(lib_gns, safe='')
        r4 = httpx.get(
            f'{AS}/api/efast/v1/folders/{enc}/sub_objects?limit=5',
            headers={'Authorization': f'Bearer {token}'}, timeout=15)
        print(f"HTTP {r4.status_code}")
        if r4.status_code == 200:
            d = r4.json()
            print(f"  {len(d.get('dirs',[]))} dirs, {len(d.get('files',[]))} files")
            print("\n>>> SUCCESS! Password grant + personal lib access works! <<<")
        else:
            print(f"  {r4.text[:200]}")
    else:
        print("\nPersonal lib not found in doc-lib/user list")
else:
    print("\nAll auth methods failed")
