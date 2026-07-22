"""Recursively collect ACL for all files/folders under a doc lib."""
from urllib.parse import quote
import httpx, json, time

T = "ory_at_svDM566dB3Z2ROK4y1iYaMF1FJXKnxqcJaLETACLhrk.liem9mdWEuWbXFqZkNkodrx1ZH_TjiClwGhAHwaZ8cs"
AS_B = "https://5j-zsgl.powerchina.cn"
ROOT = "gns://1A71734693F8464A9B8C1980D4AFBB44"
ROOT_NAME = "公司资质"

all_items = []  # {type, name, gns, acl, parent_gns}
queue = [(ROOT, ROOT_NAME, "", 0)]
total = 0

print(f"Collecting ACL for: {ROOT_NAME}")
while queue:
    gns, name, parent, depth = queue.pop(0)
    if depth > 10:
        continue

    # Get ACL for this item
    try:
        r = httpx.post(f"{AS_B}/api/eacp/v1/perm2/get",
            json={"docid": gns},
            headers={"Authorization": f"Bearer {T}"})
        if r.status_code == 200:
            acl = r.json()
            item_type = "folder" if name == ROOT_NAME or depth > 0 else "root"
            all_items.append({
                "type": item_type, "name": name, "gns": gns, "parent": parent,
                "inherit": acl.get("inherit"), "permissions": acl.get("perminfos", []),
            })
            total += 1
            print(f"  [{total}] {item_type}: {name[:40]}")
        else:
            print(f"  SKIP {name[:40]}: ACL status={r.status_code}")
    except Exception as e:
        print(f"  ERR {name[:40]}: {e}")
        continue

    # Scan children
    try:
        enc = quote(gns, safe="")
        r2 = httpx.get(f"{AS_B}/api/efast/v1/folders/{enc}/sub_objects?limit=200",
            headers={"Authorization": f"Bearer {T}"})
        if r2.status_code == 200:
            sub = r2.json()
            for d in sub.get("dirs", []):
                queue.append((d["id"], d["name"], gns, depth + 1))
            for f in sub.get("files", []):
                queue.append((f["id"], f["name"], gns, depth))
    except Exception as e:
        print(f"  Scan ERR at {name[:30]}: {e}")

    # Small delay to avoid rate limit
    time.sleep(0.1)

# Save results
with open(f"acl_{ROOT_NAME}.json", "w", encoding="utf-8") as f:
    json.dump(all_items, f, ensure_ascii=False, indent=2)

print(f"\n=== Done: {len(all_items)} items ===")
print(f"Saved to: acl_{ROOT_NAME}.json")

# Summary
users = set()
depts = set()
deny_count = 0
for item in all_items:
    for p in item.get("permissions", []):
        if p.get("deny"):
            deny_count += 1
        if p.get("accessortype") == "user":
            users.add(p.get("accessorname", ""))
        elif p.get("accessortype") == "department":
            depts.add(p.get("accessorname", ""))

print(f"Unique users: {len(users)}")
print(f"Unique departments: {len(depts)}")
print(f"Items with deny rules: {deny_count}")
