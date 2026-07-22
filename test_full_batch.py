"""Full batch test — knowledge x3, dept x2, personal x3"""
import subprocess, sys, time, json

AS_TOKEN = "ory_at_..."  # Fill in your AnyShare Browser Token
BS_COOKIE = "eyJ..."  # Fill in your BISHENG Cookie

CLIENT_ID = "your-client-id"
CLIENT_SECRET = "your-client-secret"

BASE_CMD = 'python run_sync.py'
# run_sync.py now auto-creates AnyShareAuth internally — no cmd change needed

jobs = []

# ── 知识库 (1个总space "知识库"，下面各子库作为文件夹) ─
# 第一个不用 --incremental（创建空间），后续用 --incremental（复用）
KNOWLEDGE_LIBS = [
    ("管理办法", "gns://0CBDB95E9E4340899A39C0D158E5C4F2"),
    ("公司资质", "gns://1A71734693F8464A9B8C1980D4AFBB44"),
    ("培训资料", "gns://DB436A784907494485D9AC4AAF2AFEFF"),
]
for i, (name, gns) in enumerate(KNOWLEDGE_LIBS):
    inc = "" if i == 0 else " --incremental"  # first creates space, rest reuse
    jobs.append({
        "name": f"知识库-{name}",
        "cmd": f'{BASE_CMD} "{AS_TOKEN}" "{BS_COOKIE}" "{gns}" "知识库" --ancestors "{name}" --no-root-perms --skip-download{inc}'
    })

# ── 部门文档库 (1个总space "部门文档库" + 完整树) ─
DEPT_WATER = "gns://0C9379F8E48545FEBE837679F3B4D9FA/11C780161B4D4F7BB9E227D6E332E37B"
DEPT_HQ = DEPT_WATER + "/26FBA3F5DCAB467D9BB150C19FAFE75E"
DEPT_FOLDERS = [
    ("人力资源部", DEPT_HQ + "/CB95075F74E34552B2D9577A338EDF87"),
    ("财务部", DEPT_HQ + "/0A8EFAD7ADAE4AD5AC4C5B16705C1948"),
]
for i, (name, gns) in enumerate(DEPT_FOLDERS):
    inc = "" if i == 0 else " --incremental"
    jobs.append({
        "name": f"部门库-{name}",
        "cmd": (f'{BASE_CMD} "{AS_TOKEN}" "{BS_COOKIE}" "{gns}" '
                f'"部门文档库" '
                f'--type department_doc_lib '
                f'--ancestors "组织文档库,中国水利水电第五工程局有限公司,公司总部,{name}" '
                f'--skip-download --no-root-perms{inc}')
    })

# ── 个人库 x3 ──────────────────────────────────
# Get tokens for each user via auth.py
from app.connectors.anyshare.auth import AnyShareAuth
auth = AnyShareAuth("https://5j-zsgl.powerchina.cn", CLIENT_ID, CLIENT_SECRET)

personal_users = [
    ("5jzhoujiajun", "周佳骏", "gns://3C79E073F1774D51A68A380C8DB93889"),
    ("5jchenbo", "程博", "gns://8CA0C15F67C245FDB09394BDBDFBA563"),
    ("5j_lim", "李明_test", "gns://3EF7F0473764412F9CDBB1A90AFE3BD0"),
]

for username, display_name, gns in personal_users:
    try:
        user_token = auth.get_user_token(username)
        jobs.append({
            "name": f"个人库-{display_name}",
            "cmd": f'{BASE_CMD} "{user_token}" "{BS_COOKIE}" "{gns}" "{display_name}_个人库_test" --type user_doc_lib --grant-owner "{display_name}"'
        })
    except Exception as e:
        print(f"SKIP {display_name}: token failed ({e})")

# ── Run all ─────────────────────────────────────
print(f"=== Batch test: {len(jobs)} jobs ===\n")
results = []
start = time.time()

for i, job in enumerate(jobs):
    print(f"[{i+1}/{len(jobs)}] {job['name']}")
    for attempt in range(3):
        try:
            rc = subprocess.run(job['cmd'], shell=True, cwd=r"D:\aishu\code\anyshare-sync",
                               capture_output=False, timeout=3600)
            status = "OK" if rc.returncode == 0 else f"FAIL({rc.returncode})"
            break
        except subprocess.TimeoutExpired:
            status = "TIMEOUT"
            break
        except Exception as e:
            if attempt < 2:
                print(f"  retry {attempt+1}...")
                time.sleep(10)
            else:
                status = f"ERR: {e}"
    results.append({"name": job['name'], "status": status})
    print(f"  -> {status}")

elapsed = time.time() - start
print(f"\n=== Results ({elapsed:.0f}s) ===")
ok = sum(1 for r in results if r['status'] == 'OK')
for r in results:
    print(f"  {r['status']:10s}  {r['name']}")
print(f"\n{ok}/{len(results)} passed")
