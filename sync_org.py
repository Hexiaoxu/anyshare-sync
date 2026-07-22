"""Sync AnyShare users & departments to BISHENG."""
import urllib.request, json, time, sys

T = sys.argv[1] if len(sys.argv) > 1 else input("AnyShare Token: ").strip()
BS = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ7XCJ1c2VyX2lkXCI6IDEsIFwidXNlcl9uYW1lXCI6IFwiYWRtaW5cIiwgXCJ0ZW5hbnRfaWRcIjogMSwgXCJ0b2tlbl92ZXJzaW9uXCI6IDF9IiwiZXhwIjoxNzg0MTg0MTgyLCJpc3MiOiJiaXNoZW5nIn0.P3strwbOLHEtKG_TS72MGOW-Lm8vCNsaJ5MuJtq9Csg"
BISHENG = "http://192.168.106.161:7860"
AS = "https://5j-zsgl.powerchina.cn"

def as_post(path, body={}):
    r = urllib.request.Request(f"{AS}{path}", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {T}", "Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(r).read())

def as_get(path):
    r = urllib.request.Request(f"{AS}{path}", headers={"Authorization": f"Bearer {T}"})
    return json.loads(urllib.request.urlopen(r).read())

# ── 1. Fetch AnyShare departments ─────────────────────────
print("=== AnyShare Departments ===")
depts = []
queue = ["root"]
while queue:
    pid = queue.pop(0)
    try:
        data = as_post("/api/eacp/v1/organization/getsubdepsbydepid", {"dep_id": pid})
        for d in data.get("deps", []):
            depts.append(d)
            queue.append(d["id"])
            print(f"  Dept: {d.get('name','?')} (id={d['id']})")
    except Exception as e:
        print(f"  Skip {pid}: {e}")

print(f"Total depts: {len(depts)}")

# ── 2. Fetch AnyShare users ───────────────────────────────
print("\n=== AnyShare Users ===")
users = []
total = as_post("/api/eacp/v1/organization/getallusercount", {}).get("total", 0)
print(f"Total: {total}")
start = 0
while start < min(total, 100):  # limit for test
    data = as_post("/api/eacp/v1/organization/getalluser", {"start": start, "limit": 50})
    batch = data.get("users", [])
    users.extend(batch)
    for u in batch:
        print(f"  {u.get('name','?')} (id={u['id']})")
    start += 50
    if not batch:
        break

# ── 3. Create departments in BISHENG ──────────────────────
print("\n=== Creating in BISHENG ===")
dept_map = {}
bs_cookies = {"access_token_cookie": BS}
for d in depts[:10]:  # test: first 10
    try:
        r = urllib.request.Request(f"{BISHENG}/api/v1/departments/",
            data=json.dumps({"name": d.get("name", ""), "external_id": d["id"]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        # need cookie
        import http.cookiejar
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        r2 = urllib.request.Request(f"{BISHENG}/api/v1/departments/",
            data=json.dumps({"name": d.get("name", ""), "external_id": d["id"]}).encode(),
            headers={"Content-Type": "application/json", "Cookie": f"access_token_cookie={BS}"}, method="POST")
        resp = urllib.request.urlopen(r2)
        result = json.loads(resp.read())
        dept_id = result.get("data", {}).get("id") or result.get("id")
        dept_map[d["id"]] = dept_id
        print(f"  Created dept: {d.get('name')} -> id={dept_id}")
    except Exception as e:
        print(f"  Dept FAIL {d.get('name')}: {e}")

# ── 4. Create users in BISHENG ────────────────────────────
for u in users[:20]:  # test: first 20
    try:
        data = json.dumps({
            "user_name": u.get("name", u.get("id"))[:64],
            "password": "Test123456.",
            "source": "anyshare",
            "external_id": u["id"],
        }).encode()
        r = urllib.request.Request(f"{BISHENG}/api/v1/user/create",
            data=data,
            headers={"Content-Type": "application/json", "Cookie": f"access_token_cookie={BS}"}, method="POST")
        resp = urllib.request.urlopen(r)
        result = json.loads(resp.read())
        uid = result.get("data", {}).get("user_id") or result.get("user_id")
        print(f"  Created user: {u.get('name')} -> id={uid}")
    except Exception as e:
        err = str(e)
        if "Duplicate" in err:
            print(f"  EXISTS: {u.get('name')}")
        else:
            print(f"  User FAIL {u.get('name')}: {err[:100]}")

print("\nDone")
