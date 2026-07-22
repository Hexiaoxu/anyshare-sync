"""Pull all EACPLogs from AnyShare Console"""
import httpx, json
from collections import Counter

CT = 'ory_at_-3IktmXOZA9d7_Feha24zLq1HDlRPgk6q_PPmQml95w.E5HbmRc7tBRTOp4MVi3qXr0JaaZCXHvh1eNg2oylVhU'
AS = 'https://5j-zsgl.powerchina.cn'

all_logs = []
for logType in [10, 11, 12]:
    start = 0; page = 0; max_pages = 10  # limit per logType
    while page < max_pages:
        body = [{
            'ncTGetPageLogParam': {
                'userId': '3e7a9110-3de5-11ef-bb23-de677a88534a',
                'start': start, 'limit': 500,
                'maxLogId': 9223372036854775807,
                'logType': logType,
                'levels': [], 'macs': [], 'ips': [], 'displayNames': [],
                'opTypes': [], 'msgs': [], 'exMsgs': [],
                'startDate': 1784476800000000,  # 2026-07-13
                'endDate': 1784591999999000     # 2026-07-21
            }
        }]
        try:
            r = httpx.post(
                f'{AS}/console/api/EACPLog/GetPageLog',
                json=body, timeout=60,
                headers={'Authorization': f'Bearer {CT}',
                         'Content-Type': 'application/json;charset=UTF-8'})
            if r.status_code != 200 or not r.json():
                break
            data = r.json()
            all_logs.extend(data)
            start += len(data)
            print(f'  LT{logType}: {start} logs')
            page += 1
            if len(data) < 500:
                break
        except Exception as e:
            print(f'  LT{logType} error: {e}')
            break

print(f'\nTotal: {len(all_logs)} logs')

op = Counter()
for l in all_logs:
    op[(l['logType'], l.get('opType'))] += 1

print('\nlogType -> opType:')
for (lt, optype), cnt in sorted(op.items()):
    if cnt > 0:
        s = next(l for l in all_logs if l['logType'] == lt and l.get('opType') == optype)
        msg = s.get('msg', '')[:120]
        print(f'  LT{lt}/OP{optype}: {cnt:>5} | {msg}')

with open('console_logs.json', 'w', encoding='utf-8') as f:
    json.dump(all_logs, f, ensure_ascii=False, indent=2)
print('\nSaved to console_logs.json')
