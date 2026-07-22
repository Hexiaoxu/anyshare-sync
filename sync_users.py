"""Sync AnyShare users to BISHENG — reads from anyshare_users.json."""
import json, urllib.request, urllib.error
import random, string

BS = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ7XCJ1c2VyX2lkXCI6IDEsIFwidXNlcl9uYW1lXCI6IFwiYWRtaW5cIiwgXCJ0ZW5hbnRfaWRcIjogMSwgXCJ0b2tlbl92ZXJzaW9uXCI6IDF9IiwiZXhwIjoxNzg0MTg0MTgyLCJpc3MiOiJiaXNoZW5nIn0.P3strwbOLHEtKG_TS72MGOW-Lm8vCNsaJ5MuJtq9Csg"
BS_URL = "http://192.168.106.161:7860"
COOKIES = {"access_token_cookie": BS}

with open("anyshare_users.json", "r", encoding="utf-8") as f:
    users = json.load(f)

print(f"Total users to sync: {len(users)}")

# First, get existing BISHENG users
r = urllib.request.Request(f"{BS_URL}/api/v1/user/list?page_size=200",
    headers={"Cookie": f"access_token_cookie={BS}"})
bs_users = json.loads(urllib.request.urlopen(r).read())["data"]["data"]
bs_names = {u["user_name"]: u for u in bs_users}
bs_ext = {u.get("external_id", ""): u for u in bs_users if u.get("external_id")}
print(f"BISHENG existing: {len(bs_users)}")

created = 0
for u in users[:50]:  # First 50 as test
    name = u["name"]
    ext_id = u["id"]

    if name in bs_names or ext_id in bs_ext:
        continue

    try:
        body = json.dumps({
            "user_name": name[:64] if name else f"user_{ext_id[:8]}",
            "password": "Test123456.",
            "source": "anyshare",
            "external_id": ext_id,
        }).encode()
        r = urllib.request.Request(f"{BS_URL}/api/v1/user/create",
            data=body,
            headers={"Content-Type": "application/json", "Cookie": f"access_token_cookie={BS}"},
            method="POST")
        resp = json.loads(urllib.request.urlopen(r).read())
        uid = resp.get("data", {}).get("user_id")
        created += 1
        if created <= 3:
            print(f"  Created: {name} -> id={uid}")
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:200]
        if "Duplicate" in err:
            pass
        else:
            print(f"  FAIL {name}: {err}")
    except Exception as e:
        print(f"  FAIL {name}: {str(e)[:100]}")

print(f"\nDone. Created {created} new users (skipped existing)")
print(f"Need to also run SQL to add user_tenant + user_department for these users")
