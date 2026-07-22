"""Full tree scan of a doc lib — output tree structure with ACL counts"""
import httpx, json, sys
from urllib.parse import quote
from collections import defaultdict

T = sys.argv[1] if len(sys.argv) > 1 else "ory_at_..."
GNS = sys.argv[2] if len(sys.argv) > 2 else "gns://..."
AS = "https://5j-zsgl.powerchina.cn"

tree = {}  # gns -> {"name":..., "children": {...}, "files": [...], "acl": [...]}
queue = [(GNS, "", 0, None)]
scanned = set()
all_dirs, all_files = [], []
max_depth = 3

while queue:
    gns, parent, depth, parent_key = queue.pop(0)
    if gns in scanned or depth > max_depth:
        continue
    scanned.add(gns)
    enc = quote(gns, safe="")

    r = httpx.get(
        f"{AS}/api/efast/v1/folders/{enc}/sub_objects?limit=200",
        headers={"Authorization": f"Bearer {T}"}, timeout=60)
    if r.status_code != 200:
        continue

    sub = r.json()
    dirs = sub.get("dirs", [])
    files = sub.get("files", [])

    # Get ACL
    acl = []
    try:
        r2 = httpx.post(
            f"{AS}/api/eacp/v1/perm2/get",
            json={"docid": gns},
            headers={"Authorization": f"Bearer {T}"}, timeout=30)
        if r2.status_code == 200:
            acl = r2.json().get("perminfos", [])
    except:
        pass

    # Store in tree
    name = gns.rsplit("/", 1)[-1]
    node = {"name": name, "dirs": len(dirs), "files": len(files),
            "acl_entries": len(acl), "inherit": r.json().get("inherit", False) if dirs else None,
            "depth": depth}

    for d in dirs:
        all_dirs.append(d)
        queue.append((d["id"], gns, depth + 1, gns))
    for f in files:
        all_files.append(f)

    # Pretty print
    indent = "  " * depth
    tree_char = "├── " if depth > 0 else ""
    print(f"{indent}{tree_char}{name[:40]} ({len(dirs)}D/{len(files)}F, ACL:{len(acl)})")

    total = len(all_dirs) + len(all_files)
    if total % 50 == 0:
        print(f"  [{total} items scanned so far...]", end="\r")

print(f"\n=== Tree Complete ===")
print(f"Total: {len(all_dirs)} dirs + {len(all_files)} files = {len(all_dirs)+len(all_files)} items")
print(f"Max depth: {max_depth}")

# ACL summary
acl_total = 0
acl_items = 0
users = set()
depts = set()
for gns in list(scanned)[:len(all_dirs)+1]:
    try:
        r = httpx.post(
            f"{AS}/api/eacp/v1/perm2/get",
            json={"docid": gns},
            headers={"Authorization": f"Bearer {T}"}, timeout=15)
        if r.status_code == 200:
            perms = r.json().get("perminfos", [])
            if perms:
                acl_items += 1
                acl_total += len(perms)
                for p in perms:
                    aname = p.get("accessorname", "")
                    if p.get("accessortype") == "user":
                        users.add(aname.split("/**eisoo**/")[1] if "/**eisoo**/" in aname else aname)
                    else:
                        depts.add(aname)
    except:
        pass

print(f"\nACL total: {acl_total} entries across {acl_items} items")
print(f"Unique users: {len(users)}")
for u in sorted(users):
    print(f"  - {u}")
print(f"Unique departments: {len(depts)}")
for d in sorted(depts):
    print(f"  - {d}")
