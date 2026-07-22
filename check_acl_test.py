"""Check ACL for test libraries"""
import httpx
T='ory_at_s_lFYRoKyzQG3pBsmMm3UcVnt8kp1lIaZ4gZZwVU32M.G6au6OX6uxNT_1-e2imqMyqOdyyCyI5306z74i2UrqY'
AS='https://5j-zsgl.powerchina.cn'

tests = [
    ("管理办法", "gns://0CBDB95E9E4340899A39C0D158E5C4F2"),
    ("公司资质", "gns://1A71734693F8464A9B8C1980D4AFBB44"),
    ("期刊文献", "gns://EA81FBA55D924C4C94270749A3B09FD4"),
    ("培训资料", "gns://DB436A784907494485D9AC4AAF2AFEFF"),
    ("程博个人库", "gns://8CA0C15F67C245FDB09394BDBDFBA563"),
]
for name, gns in tests:
    r = httpx.post(f'{AS}/api/eacp/v1/perm2/get', json={'docid': gns},
                   headers={'Authorization': f'Bearer {T}'}, timeout=15)
    perms = r.json().get('perminfos', [])
    users = sum(1 for p in perms if p.get('accessortype') == 'user')
    depts = sum(1 for p in perms if p.get('accessortype') == 'department')
    print(f"{name}: {len(perms)} entries ({users} users, {depts} depts)")
    if perms:
        for p in perms[:2]:
            print(f"  {p.get('accessortype')}: {p.get('accessorname','?')[:50]}")
