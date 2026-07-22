"""Test App Token scope — what CAN it access?"""
import httpx
from urllib.parse import quote

T = 'ory_at_paKAEhspkqsY-sxl79NHrNZix3k4u5_xR-yhXB4KcmQ.7_ECfwSqDTn7mzsMawqp12uYjCYyREE964mOLOlmGsw'
AS = 'https://5j-zsgl.powerchina.cn'

tests = [
    ("List knowledge libs", "GET",
     f"{AS}/api/efast/v1/doc-lib/knowledge?offset=0&limit=3"),
    ("List department libs", "GET",
     f"{AS}/api/efast/v1/doc-lib/department?offset=0&limit=3"),
    ("List user libs", "GET",
     f"{AS}/api/efast/v1/doc-lib/user?offset=0&limit=3"),
    ("Knowledge lib scan (公司资质)", "GET",
     f"{AS}/api/efast/v1/folders/{quote('gns://1A71734693F8464A9B8C1980D4AFBB44', safe='')}/sub_objects?limit=3"),
    ("Personal lib scan (5j_lim)", "GET",
     f"{AS}/api/efast/v1/folders/{quote('gns://3EF7F0473764412F9CDBB1A90AFE3BD0', safe='')}/sub_objects?limit=3"),
    ("Console API", "POST",
     f"{AS}/console/api/ShareMgnt/GetCSFLevels"),
]

for label, method, url in tests:
    try:
        if method == "GET":
            r = httpx.get(url, headers={'Authorization': f'Bearer {T}'}, timeout=15)
        else:
            r = httpx.post(url, headers={'Authorization': f'Bearer {T}'}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                print(f"✅ {label}: {len(data)} items")
            elif 'entries' in data:
                print(f"✅ {label}: {len(data['entries'])} entries")
            elif 'dirs' in data:
                print(f"✅ {label}: {len(data['dirs'])} dirs, {len(data['files'])} files")
            else:
                print(f"✅ {label}: {str(data)[:80]}")
        else:
            print(f"❌ {label}: HTTP {r.status_code} {r.text[:100]}")
    except Exception as e:
        print(f"❌ {label}: {e}")
