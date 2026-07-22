"""Test each grant individually to find the failing one"""
import httpx
BS = 'http://192.168.106.161:3001'
CK = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ7XCJ1c2VyX2lkXCI6IDEsIFwidXNlcl9uYW1lXCI6IFwiYWRtaW5cIiwgXCJ0ZW5hbnRfaWRcIjogMSwgXCJ0b2tlbl92ZXJzaW9uXCI6IDF9IiwiZXhwIjoxNzg0MjUyNjc0LCJpc3MiOiJiaXNoZW5nIn0.NF_QfO-80aRH3DjYW8Aaql-F5FegeMEGg2GAoIYTeO4'

from app.connectors.bisheng.client import BishengClient, BishengApiError
from app.connectors.bisheng.permission import BishengPermission

bs = BishengClient(BS, CK, timeout=60)
perm = BishengPermission(bs)

# Use a fresh folder — folder 344 (发明专利) to avoid conflicts
tests = [
    ('user viewer', {'subject_type': 'user', 'subject_id': 7479, 'relation': 'viewer'}),
    ('user editor', {'subject_type': 'user', 'subject_id': 7881, 'relation': 'editor'}),
    ('dept editor', {'subject_type': 'department', 'subject_id': 11, 'relation': 'editor', 'include_children': True}),
    ('dept no children', {'subject_type': 'department', 'subject_id': 11, 'relation': 'editor'}),
    ('user with model_id', {'subject_type': 'user', 'subject_id': 2344, 'relation': 'editor', 'model_id': None}),
]

for label, grant in tests:
    print(f'\nTest: {label} -> {grant}')
    try:
        r = httpx.post(
            f'{BS}/api/v1/permissions/resources/folder/344/authorize',
            json={'grants': [grant], 'revokes': []},
            cookies={'access_token_cookie': CK},
            timeout=60)
        body = r.json()
        ok = body.get('status_code') == 200
        print(f'  HTTP:{r.status_code} biz_code:{body.get("status_code")} msg:{body.get("status_message","")}')
        if not ok:
            print(f'  FAIL: {body}')
    except Exception as e:
        print(f'  EXCEPTION: {e}')

# Verify folder 344
print()
r = httpx.get(f'{BS}/api/v1/permissions/resources/folder/344/permissions',
    cookies={'access_token_cookie': CK})
print(f'Final perms: {[(p["subject_type"],p["subject_id"],p["relation"]) for p in r.json().get("data",[])]}')
