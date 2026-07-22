"""Debug authentication API for user token"""
import httpx, base64

CID = '7b98e7b6-f35e-4613-aeed-5b13112b0ff8'
CSEC = 'Test123.'
AUTH = base64.b64encode(f'{CID}:{CSEC}'.encode()).decode()
AS = 'https://5j-zsgl.powerchina.cn'

# Try getting user token for various accounts
for user in ['5j_lim', '周佳骏', '罗运军', '5jzhoujiajun']:
    r = httpx.post(
        f'{AS}/api/authentication/v1/access_token',
        json={'account': user},
        headers={'Authorization': f'Basic {AUTH}',
                 'Content-Type': 'application/json'},
        timeout=15)
    print(f"{user:20s}  HTTP {r.status_code}  {r.text[:200]}")
