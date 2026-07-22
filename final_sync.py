"""Final sync: personal lib → BISHENG with username-based space name."""
import json, time, shutil, uuid, httpx
from pathlib import Path
from urllib.parse import quote

AS = "ory_at_7jv-of8aq8uNFpjIMQSWcVlkiYJeAth4_j_odh5AwH8.7cJwO2RymjdYBPTDBkA_dqIQw8xlXG_an6B65GcSrlg"
BS = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ7XCJ1c2VyX2lkXCI6IDEsIFwidXNlcl9uYW1lXCI6IFwiYWRtaW5cIiwgXCJ0ZW5hbnRfaWRcIjogMSwgXCJ0b2tlbl92ZXJzaW9uXCI6IDF9IiwiZXhwIjoxNzg0MTg0MTgyLCJpc3MiOiJiaXNoZW5nIn0.P3strwbOLHEtKG_TS72MGOW-Lm8vCNsaJ5MuJtq9Csg"
AS_B = "https://5j-zsgl.powerchina.cn"
BS_B = "http://192.168.106.161:7860"

print("=== Cleaning old spaces ===")
with httpx.Client(timeout=30) as c:
    r = c.get(f"{BS_B}/api/v1/knowledge/space/mine", cookies={"access_token_cookie": BS})
    for sp in r.json()["data"]:
        name = sp.get("name", "")
        if "5jliming1" in name or name.lower().startswith("test") or "E2E" in name:
            print(f"  Delete {name}")
            c.delete(f"{BS_B}/api/v1/knowledge/space/{sp['id']}", cookies={"access_token_cookie": BS})

USER_NAME = "5jliming1"
print(f"\n=== Creating space: {USER_NAME} ===")
r = httpx.post(f"{BS_B}/api/v1/knowledge/space",
    json={"name": USER_NAME, "description": "AnyShare个人文档库", "auth_type": "public"},
    cookies={"access_token_cookie": BS})
SP = r.json()["data"]["id"]
print(f"  Space id={SP}")

print(f"\n=== Scanning ===")
LIB = "gns://110F8E071F0243AEBDB4DFD59F52D131"
r = httpx.get(f"{AS_B}/api/efast/v1/folders/{quote(LIB, safe='')}/sub_objects?limit=100&sort=name&direction=asc",
    headers={"Authorization": f"Bearer {AS}"})
files = r.json()["files"]
print(f"  {len(files)} files")
for f in files:
    print(f"    {f['name']} ({f.get('size', 0)}b)")

td = Path.home() / "AppData" / "Local" / "Temp" / "as_final" / uuid.uuid4().hex[:8]
td.mkdir(parents=True, exist_ok=True)
ok = ng = 0

for i, f in enumerate(files):
    nm, did = f["name"], f["id"]
    print(f"[{i+1}/{len(files)}] {nm[:50]}", end=" ", flush=True)
    try:
        r = httpx.post(f"{AS_B}/api/efast/v1/file/osdownload",
            json={"docid": did, "rev": "", "authtype": "QUERY_STRING", "savename": nm, "usehttps": True},
            headers={"Authorization": f"Bearer {AS}"})
        a = r.json()["authrequest"]
        hh = {}
        for h in a[2:]:
            if ": " in h:
                k, v = h.split(": ", 1)
                hh[k] = v
        sf = "".join(c for c in nm if c.isalnum() or c in "._-()（）")
        lp = td / sf
        with httpx.Client(timeout=120) as cc:
            with cc.stream(a[0], a[1], headers=hh) as rr:
                rr.raise_for_status()
                with open(lp, "wb") as ff:
                    for ch in rr.iter_bytes(65536):
                        ff.write(ch)
        print(f"DL:{lp.stat().st_size}", end=" ", flush=True)
        # Upload
        r = httpx.post(f"{BS_B}/api/v1/knowledge/upload/{SP}",
            files={"file": open(lp, "rb")},
            cookies={"access_token_cookie": BS})
        fp = r.json()["data"]["file_path"]
        # Register
        r = httpx.post(f"{BS_B}/api/v1/knowledge/space/{SP}/files",
            json={"file_path": [fp], "parent_id": None},
            cookies={"access_token_cookie": BS})
        fid = r.json()["data"][0]["id"]
        print(f"REG:{fid}", end=" ", flush=True)
        # Wait
        for w in [5, 10, 15, 20, 30]:
            time.sleep(w)
            r = httpx.get(f"{BS_B}/api/v1/knowledge/space/{SP}/children?page=1&page_size=200",
                cookies={"access_token_cookie": BS})
            for it in r.json()["data"]["data"]:
                if it["id"] == fid:
                    if it["status"] == 2:
                        print("OK")
                        ok += 1
                        break
                    elif it["status"] in (3, 7):
                        print(f"FAIL({it['status']})")
                        ng += 1
                        break
            else:
                continue
            break
        else:
            print("TIMEOUT")
            ng += 1
        lp.unlink(missing_ok=True)
    except Exception as e:
        print(f"ERR:{str(e)[:80]}")
        ng += 1

shutil.rmtree(td, ignore_errors=True)
print(f"\n=== DONE: {ok}/{len(files)} OK, {ng} failed ===")
print(f"Space: {USER_NAME} (id={SP})")
print(f"View: http://192.168.106.161:3001")
