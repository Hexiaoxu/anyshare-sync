"""Full org sync: AnyShare -> BISHENG.

1. Fetch full department tree + all users from AnyShare
2. Create departments in BISHENG
3. Create users in BISHENG
4. Write principal_mapping records
5. (Manual step) Assign users to departments

Usage:
  python sync_org_full.py <as_console_token> <as_regular_token> <bs_cookie>
"""
import sys, time, uuid, json, urllib.request, urllib.error, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("org-sync")

# ── Config ─────────────────────────────────────────────────
AS_CONSOLE_T = sys.argv[1] if len(sys.argv) > 1 else input("AnyShare Console Token: ").strip()
AS_REGULAR_T = sys.argv[2] if len(sys.argv) > 2 else input("AnyShare Regular Token: ").strip()
BS_COOKIE = sys.argv[3] if len(sys.argv) > 3 else input("BISHENG Cookie: ").strip()

AS_BASE = "https://5j-zsgl.powerchina.cn"
BS_BASE = "http://192.168.106.161:7860"
ROOT_DEPT_ID = "5eaa9448-7992-11ee-beda-6682a185520e"
ROOT_DEPT_NAME = "中国水利水电第五工程局有限公司"

sys.path.insert(0, ".")

from app.models import init_db, get_session
from app.models.principal_mapping import SyncPrincipalMapping
from sqlmodel import select

BS_COOKIES = {"access_token_cookie": BS_COOKIE}


def as_console_post(path, body):
    r = urllib.request.Request(f"{AS_BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {AS_CONSOLE_T}", "Content-Type": "application/json"},
        method="POST")
    return json.loads(urllib.request.urlopen(r).read())


# ── 1. Fetch AnyShare Department Tree ──────────────────────
logger.info("=== Step 1: Fetch AnyShare Department Tree ===")
all_depts = []
queue = [[ROOT_DEPT_ID]]

while queue:
    batch = queue.pop(0)
    try:
        children = as_console_post("/console/api/ShareMgnt/Usrm_GetSubDepartments", batch)
    except Exception as e:
        logger.error(f"Failed at batch: {e}")
        continue
    next_batch = []
    for c in children:
        d = {
            "id": c["id"], "name": c.get("name",""),
            "sub_dept_count": c.get("subDepartmentCount",0),
            "sub_user_count": c.get("subUserCount",0),
        }
        all_depts.append(d)
        if c.get("subDepartmentCount", 0) > 0:
            next_batch.append(c["id"])
    if next_batch:
        queue.append(next_batch)
    logger.info(f"  Depth batch: {len(children)} depts, total={len(all_depts)}")

logger.info(f"Total departments: {len(all_depts)}")

# ── 2. Fetch Users per Department ───────────────────────────
logger.info("=== Step 2: Fetch Users ===")
all_users = {}
for d in [{"id": ROOT_DEPT_ID, "name": ROOT_DEPT_NAME, "sub_user_count": 999}] + all_depts:
    if d.get("sub_user_count", 0) == 0:
        continue
    try:
        users = as_console_post("/console/api/ShareMgnt/Usrm_GetSubUsers", [d["id"]])
        for u in users:
            uid = u["id"]
            if uid not in all_users:
                all_users[uid] = {
                    "id": uid, "name": u.get("name",""),
                    "dept_ids": [d["id"]], "email": u.get("email",""),
                    "phone": u.get("phone", u.get("mobile","")),
                }
            else:
                all_users[uid]["dept_ids"].append(d["id"])
        logger.info(f"  {d['name']}: {len(users)} users")
    except Exception as e:
        logger.warning(f"  {d['name']}: FAIL - {e}")

logger.info(f"Total unique users: {len(all_users)}")

# ── 3. Get existing BISHENG state ───────────────────────────
logger.info("=== Step 3: BISHENG current state ===")
import http.cookiejar
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Get existing users
r = urllib.request.Request(f"{BS_BASE}/api/v1/user/list?page_size=200",
    headers={"Cookie": f"access_token_cookie={BS_COOKIE}"})
bs_users = json.loads(urllib.request.urlopen(r).read())["data"]["data"]
bs_user_names = {u["user_name"]: u for u in bs_users}
bs_user_ext_ids = {u.get("external_id",""): u for u in bs_users if u.get("external_id")}
logger.info(f"BISHENG existing users: {len(bs_users)}")

# Get existing departments
r = urllib.request.Request(f"{BS_BASE}/api/v1/departments/",
    data=b"{}",
    headers={"Content-Type": "application/json", "Cookie": f"access_token_cookie={BS_COOKIE}"},
    method="POST")
bs_depts = json.loads(urllib.request.urlopen(r).read()).get("data", {}).get("data", [])
bs_dept_names = {d["name"]: d for d in bs_depts}
logger.info(f"BISHENG existing departments: {len(bs_depts)}")

# ── 4. Create Departments in BISHENG ────────────────────────
logger.info("=== Step 4: Create Departments ===")
dept_map = {}
created = 0
for d in all_depts:
    name = d["name"]
    if name in bs_dept_names:
        dept_map[d["id"]] = bs_dept_names[name]["id"]
        continue
    try:
        body = json.dumps({"name": name, "external_id": d["id"]}).encode()
        r = urllib.request.Request(f"{BS_BASE}/api/v1/departments/",
            data=body,
            headers={"Content-Type": "application/json", "Cookie": f"access_token_cookie={BS_COOKIE}"},
            method="POST")
        resp = json.loads(urllib.request.urlopen(r).read())
        dept_id = resp.get("data", {}).get("id") or resp.get("id")
        dept_map[d["id"]] = dept_id
        created += 1
        if created <= 5:
            logger.info(f"  Created: {name} -> id={dept_id}")
    except Exception as e:
        err = str(e)[:150]
        if "Duplicate" in err or "already" in err:
            logger.debug(f"  Dup: {name}")
        else:
            logger.warning(f"  FAIL: {name}: {err}")

logger.info(f"Departments created: {created}")

# ── 5. Create Users in BISHENG ──────────────────────────────
logger.info("=== Step 5: Create Users ===")
created = 0
init_db()
for uid, u in list(all_users.items())[:100]:  # Test: first 100
    name = u["name"]
    if name in bs_user_names or uid in bs_user_ext_ids:
        # Already exists — write mapping
        bs_uid = bs_user_names.get(name, bs_user_ext_ids.get(uid, {})).get("user_id")
        if bs_uid:
            with get_session() as s:
                existing = s.exec(select(SyncPrincipalMapping).where(
                    SyncPrincipalMapping.source_id == uid
                )).first()
                if not existing:
                    s.add(SyncPrincipalMapping(
                        source_id=uid, source_type="user", source_name=name,
                        target_id=bs_uid, status="mapped", match_method="external_id",
                    ))
                    s.commit()
        continue

    try:
        # Create via BISHENG API
        body = json.dumps({
            "user_name": name[:64],
            "password": "Test123456.",
            "source": "anyshare",
            "external_id": uid,
        }).encode()
        r = urllib.request.Request(f"{BS_BASE}/api/v1/user/create",
            data=body,
            headers={"Content-Type": "application/json", "Cookie": f"access_token_cookie={BS_COOKIE}"},
            method="POST")
        resp = json.loads(urllib.request.urlopen(r).read())
        bs_uid = resp.get("data", {}).get("user_id") or resp.get("user_id")
        created += 1

        # Write mapping
        with get_session() as s:
            s.add(SyncPrincipalMapping(
                source_id=uid, source_type="user", source_name=name,
                target_id=bs_uid, status="mapped", match_method="api_create",
            ))
            s.commit()

        if created <= 5:
            logger.info(f"  Created: {name} -> user_id={bs_uid}")
    except Exception as e:
        err = str(e)
        if "Duplicate" in err or "1062" in err:
            logger.debug(f"  Dup: {name}")
        else:
            logger.warning(f"  FAIL {name}: {err[:100]}")

logger.info(f"Users created: {created}")

# ── Summary ─────────────────────────────────────────────────
logger.info("=== Sync Complete ===")
with get_session() as s:
    from sqlmodel import func
    count = s.exec(select(func.count()).select_from(SyncPrincipalMapping)).one()
    logger.info(f"Principal mappings: {count}")

print(f"\nDone. Run again with larger batch to sync all {len(all_users)} users.")
