"""Find department doc lib: 组织文档库 → 中国水利水电第五工程局有限公司 → 公司总部文档"""
import httpx, json
from urllib.parse import quote

T = 'ory_at_g2DFuwYT2DLRIYgRmNxMNDtXU_u6pKA79ZB923bxWbw.8U996AXqk32pvhsAIOSkB5DGimxXHzQzlIM8TptOS4k'
AS = 'https://5j-zsgl.powerchina.cn'

print("=== 1. Department doc libs ===")
r = httpx.get(f'{AS}/api/efast/v1/doc-lib/department?offset=0&limit=100',
    headers={'Authorization': f'Bearer {T}'})
if r.status_code != 200:
    print(f'FAIL: {r.status_code} {r.text[:200]}')
    exit()

dept_libs = r.json().get('entries', [])
print(f'Found {len(dept_libs)} department doc libs')
for e in dept_libs:
    name = e.get('name', '?')
    gns = e['id']
    print(f'  {name} -> {gns}')
    # Check if this is 组织文档库
    if '组织' in name:
        print(f'\n=== 2. Sub-objects of 组织文档库 ===')
        enc = quote(gns, safe='')
        r2 = httpx.get(f'{AS}/api/efast/v1/folders/{enc}/sub_objects?limit=200',
            headers={'Authorization': f'Bearer {T}'})
        if r2.status_code == 200:
            for d in r2.json().get('dirs', []):
                dname = d.get('name', '?')
                dgns = d['id']
                print(f'  DIR: {dname} -> {dgns}')
                if '水电' in dname or '第五工程' in dname:
                    print(f'\n=== 3. Sub-objects of 中国水利水电第五工程局有限公司 ===')
                    enc2 = quote(dgns, safe='')
                    r3 = httpx.get(f'{AS}/api/efast/v1/folders/{enc2}/sub_objects?limit=200',
                        headers={'Authorization': f'Bearer {T}'})
                    if r3.status_code == 200:
                        for dd in r3.json().get('dirs', []):
                            ddname = dd.get('name', '?')
                            ddgns = dd['id']
                            print(f'    DIR: {ddname} -> {ddgns}')
                            if '总部' in ddname or '公司总部' in ddname:
                                print(f'\n>>> TARGET FOUND: {ddname}')
                                print(f'>>> GNS: {ddgns}')
        break
