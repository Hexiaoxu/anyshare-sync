"""Test if App Token can scan knowledge libraries"""
import httpx
from urllib.parse import quote
from app.connectors.anyshare.auth import AnyShareAuth

auth = AnyShareAuth(
    'https://5j-zsgl.powerchina.cn',
    '7b98e7b6-f35e-4613-aeed-5b13112b0ff8', 'Test123.')
app_token = auth.get_app_token()
AS = 'https://5j-zsgl.powerchina.cn'

tests = [
    ("管理办法", "gns://0CBDB95E9E4340899A39C0D158E5C4F2"),
    ("公司资质", "gns://1A71734693F8464A9B8C1980D4AFBB44"),
]

for name, gns in tests:
    enc = quote(gns, safe='')
    r = httpx.get(
        f'{AS}/api/efast/v1/folders/{enc}/sub_objects?limit=5',
        headers={'Authorization': f'Bearer {app_token}'}, timeout=15)
    if r.status_code == 200:
        d = r.json()
        print(f"{name}: HTTP 200, {len(d.get('dirs',[]))} dirs, {len(d.get('files',[]))} files — App Token works!")
    else:
        print(f"{name}: HTTP {r.status_code} — App Token CANNOT scan")
