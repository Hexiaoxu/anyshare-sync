"""Search for a user's personal lib by name (Chinese or username)"""
import sys, httpx
from app.connectors.anyshare.auth import AnyShareAuth

NAME = sys.argv[1] if len(sys.argv) > 1 else input("Username/display name: ").strip()

auth = AnyShareAuth(
    'https://5j-zsgl.powerchina.cn',
    '7b98e7b6-f35e-4613-aeed-5b13112b0ff8', 'Test123.')
token = auth.get_app_token()
AS = 'https://5j-zsgl.powerchina.cn'

print(f"Searching: {NAME}")
offset = 0
found = []
while True:
    r = httpx.get(
        f'{AS}/api/efast/v1/doc-lib/user?offset={offset}&limit=200',
        headers={'Authorization': f'Bearer {token}'}, timeout=30)
    if r.status_code != 200:
        print(f"HTTP {r.status_code}")
        break
    entries = r.json().get('entries', [])
    if not entries:
        break
    for e in entries:
        owners = e.get('owned_by', [])
        if owners:
            oname = owners[0].get('name', '')
            oid = owners[0].get('id', '')
            if NAME.lower() in oname.lower() or NAME.lower() in oid.lower():
                found.append({'user': oname, 'id': oid, 'gns': e['id']})
                print(f"  {oname} ({oid})  ->  {e['id']}")
    offset += len(entries)
    if len(entries) < 200 or offset > 5000:
        break

if not found:
    print("Not found")
else:
    print(f"\nFound: {len(found)}")
    # Show migration command for first match
    name = found[0]['user']
    gns = found[0]['gns']
    print(f"\nFirst match: {name}")
    BS = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ7XCJ1c2VyX2lkXCI6IDEsIFwidXNlcl9uYW1lXCI6IFwiYWRtaW5cIiwgXCJ0ZW5hbnRfaWRcIjogMSwgXCJ0b2tlbl92ZXJzaW9uXCI6IDF9IiwiZXhwIjoxNzg0NTk5Mjc2LCJpc3MiOiJiaXNoZW5nIn0.EDbuU7W_Lehk8bUXtGbPm7OmcXqOB1a9WnMvmFAXP1I"
    print("Getting user token...")
    try:
        ut = auth.get_user_token(name)
        print(f"\nTo migrate:")
        print(f'python run_sync.py "{ut}" "{BS}" "{gns}" "{name}_个人库" --type user_doc_lib')
    except Exception as e:
        print(f"Cannot get token for {name}: {e}")
        print(f"\nTo check manually: python check_user_lib.py {name}")
