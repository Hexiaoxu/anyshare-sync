"""Test password-based login and personal lib access for 5j_lim"""
import httpx
from urllib.parse import quote

AS = 'https://5j-zsgl.powerchina.cn'
USER = '5j_lim'
PW = 'Liming123.'
LIB = 'gns://3EF7F0473764412F9CDBB1A90AFE3BD0'

tests = [
    ('OAuth2 password', AS + '/oauth2/token',
     {'data': {'grant_type': 'password', 'username': USER, 'password': PW, 'scope': 'all'},
      'headers': {'Content-Type': 'application/x-www-form-urlencoded'}}),
    ('access_token API', AS + '/api/authentication/v1/access_token',
     {'json': {'account': USER, 'password': PW}}),
    ('login API', AS + '/api/efast/v1/login',
     {'json': {'username': USER, 'password': PW}}),
]

for label, url, body in tests:
    try:
        r = httpx.post(url, timeout=15, **body)
        print(f'{label}: HTTP {r.status_code}')
        print(f'  {r.text[:300]}')

        # If got a token, test personal lib access
        token = r.json().get('access_token', '')
        if token:
            enc = quote(LIB, safe='')
            r2 = httpx.get(
                f'{AS}/api/efast/v1/folders/{enc}/sub_objects?limit=5',
                headers={'Authorization': f'Bearer {token}'}, timeout=30)
            print(f'  -> Access personal lib: HTTP {r2.status_code}')
            if r2.status_code == 200:
                d = r2.json()
                print(f'  -> {len(d.get("dirs",[]))} dirs, {len(d.get("files",[]))} files')
                print(f'  -> SUCCESS - password login works for personal lib migration!')
    except Exception as e:
        print(f'{label}: Error - {e}')
    print()
