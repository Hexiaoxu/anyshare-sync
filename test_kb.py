"""Test KB ACL/permissions"""
from urllib.parse import quote
import httpx, json

T = "ory_at_svDM566dB3Z2ROK4y1iYaMF1FJXKnxqcJaLETACLhrk.liem9mdWEuWbXFqZkNkodrx1ZH_TjiClwGhAHwaZ8cs"
AS_B = "https://5j-zsgl.powerchina.cn"

ROOT = "gns://1A71734693F8464A9B8C1980D4AFBB44"

# Get first subdir
enc = quote(ROOT, safe="")
r = httpx.get(f"{AS_B}/api/efast/v1/folders/{enc}/sub_objects?limit=5", headers={"Authorization": f"Bearer {T}"})
dirs = r.json().get("dirs", [])

if dirs:
    d = dirs[0]
    enc2 = quote(d["id"], safe="")
    r2 = httpx.get(f"{AS_B}/api/efast/v1/folders/{enc2}/sub_objects?limit=5", headers={"Authorization": f"Bearer {T}"})
    files = r2.json().get("files", [])
    if files:
        f = files[0]
        print(f"File: {f['name']}")
        print(f"GNS: {f['id']}")

        # Test ACL
        print(f"\n=== perm2/get ===")
        r3 = httpx.post(f"{AS_B}/api/eacp/v1/perm2/get",
            json={"docid": f["id"]},
            headers={"Authorization": f"Bearer {T}"})
        if r3.status_code == 200:
            acl = r3.json()
            print(f"Status: 200")
            print(f"inherit: {acl.get('inherit')}")
            for p in acl.get("perminfos", []):
                print(f"  {p.get('accessortype')}: {p.get('accessorname','?')[:30]}")
                print(f"    allow: {p.get('allow')}")
                print(f"    deny: {p.get('deny')}")
                print(f"    endtime: {p.get('endtime')}")
        else:
            print(f"FAIL: {r3.status_code}")

        # Also test perm1/checkall
        print(f"\n=== perm1/checkall ===")
        r4 = httpx.post(f"{AS_B}/api/eacp/v1/perm1/checkall",
            json={"docid": f["id"]},
            headers={"Authorization": f"Bearer {T}"})
        if r4.status_code == 200:
            print(json.dumps(r4.json(), indent=2, ensure_ascii=False)[:500])
        else:
            print(f"FAIL: {r4.status_code}")
