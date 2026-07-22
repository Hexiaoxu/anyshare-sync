"""Try to get token by simulating browser login flow"""
import httpx
import re

AS = 'https://5j-zsgl.powerchina.cn'
USER = '5j_lim'
PW = 'Liming123.'

client = httpx.Client(timeout=30, follow_redirects=True)

# 1. Get login page to extract CSRF token / form action
print('=== Step 1: Get login page ===')
r = client.get(f'{AS}/')
print(f'HTTP {r.status_code}')
# Find login form
for line in r.text.split('\n'):
    if any(kw in line.lower() for kw in ['login', 'oauth2', 'authorize', 'token', 'password']):
        print(f'  {line.strip()[:150]}')
    if 'action' in line.lower():
        print(f'  FORM: {line.strip()[:200]}')

# 2. Try common login endpoints
for path in ['/login', '/api/login', '/oauth2/authorize', '/signin', '/auth/login']:
    try:
        r2 = client.get(f'{AS}{path}')
        if r2.status_code < 500:
            print(f'\n{path}: HTTP {r2.status_code}')
            # Check if it redirects to a login page
            if 'login' in r2.text.lower() or 'password' in r2.text.lower():
                print(f'  Contains login form')
    except Exception:
        pass

# 3. Try direct login POST
for path, body_type in [
    ('/login', {'username': USER, 'password': PW}),
    ('/api/login', {'username': USER, 'password': PW}),
    ('/oauth2/authorize', {'response_type': 'token', 'client_id': 'browser', 'username': USER, 'password': PW}),
    ('/', {'account': USER, 'password': PW, 'rememberMe': True}),
]:
    try:
        headers = {'Content-Type': 'application/json'}
        r3 = client.post(f'{AS}{path}', json=body_type, headers=headers)
        if r3.status_code < 500:
            print(f'\nPOST {path}: HTTP {r3.status_code}')
            # Check response for token
            text = r3.text[:500]
            if 'token' in text.lower() or 'access' in text.lower():
                print(f'  {text}')
            # Check cookies
            for cookie_name in ['client.oauth2_token', 'JSESSIONID', 'token']:
                val = client.cookies.get(cookie_name)
                if val:
                    print(f'  Cookie {cookie_name}={val[:50]}...')
    except Exception as e:
        print(f'POST {path}: {e}')
