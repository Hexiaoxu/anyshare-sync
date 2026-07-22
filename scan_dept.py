"""Quick scan of department doc lib folder"""
import httpx, json
from urllib.parse import quote

T = 'ory_at_g2DFuwYT2DLRIYgRmNxMNDtXU_u6pKA79ZB923bxWbw.8U996AXqk32pvhsAIOSkB5DGimxXHzQzlIM8TptOS4k'
AS = 'https://5j-zsgl.powerchina.cn'
ROOT = 'gns://0C9379F8E48545FEBE837679F3B4D9FA/11C780161B4D4F7BB9E227D6E332E37B/26FBA3F5DCAB467D9BB150C19FAFE75E'

all_dirs = []
all_files = []
queue = [(ROOT, "")]
scanned = set()

while queue:
    gns, parent = queue.pop(0)
    if gns in scanned:
        continue
    scanned.add(gns)
    enc = quote(gns, safe="")
    r = httpx.get(f'{AS}/api/efast/v1/folders/{enc}/sub_objects?limit=200',
        headers={'Authorization': f'Bearer {T}'}, timeout=60)
    if r.status_code != 200:
        print(f'FAIL at {gns[-20:]}: {r.status_code}')
        continue
    sub = r.json()
    for d in sub.get('dirs', []):
        all_dirs.append(d)
        queue.append((d['id'], gns))
    for f in sub.get('files', []):
        all_files.append(f)
    print(f'Scanned: {len(all_dirs)} dirs, {len(all_files)} files', end='\r')

print(f'\nTotal: {len(all_dirs)} dirs, {len(all_files)} files')
for d in all_dirs:
    print(f'  DIR: {d["name"][:50]}')
for f in all_files[:10]:
    print(f'  FILE: {f["name"][:60]} ({f.get("size",0)}b)')
if len(all_files) > 10:
    print(f'  ... and {len(all_files)-10} more files')

# Also quick check: ACL for root
print(f'\n=== Root ACL ===')
r = httpx.post(f'{AS}/api/eacp/v1/perm2/get',
    json={'docid': ROOT},
    headers={'Authorization': f'Bearer {T}'}, timeout=30)
if r.status_code == 200:
    acl = r.json()
    perms = acl.get('perminfos', [])
    print(f'{len(perms)} entries, inherit={acl.get("inherit")}')
    for p in perms:
        print(f'  {p.get("accessortype")}: {p.get("accessorname","")[:40]} allow={p.get("allow")}')
