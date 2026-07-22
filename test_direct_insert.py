"""Test direct INSERT into BISHENG_FOR_AISHU with dmPython"""
import dmPython
conn = dmPython.connect(user='SYSDBA', password='6o+%s3z2NK7J',
                        server='192.168.107.9', port=5236)
cur = conn.cursor()

# Try different table name formats
tests = [
    'INSERT INTO "BISHENG_FOR_AISHU"."ANYSHARE_SYNC_SCOPE_CONFIG" ("TENANT_ID","SOURCE_TYPE","SOURCE_ID","SOURCE_NAME") VALUES (:1,:2,:3,:4)',
    'INSERT INTO "BISHENG_FOR_AISHU"."ANYSHARE_SYNC_SCOPE_CONFIG" ("source_type","source_id","source_name") VALUES (:1,:2,:3)',
    'INSERT INTO BISHENG_FOR_AISHU.ANYSHARE_SYNC_SCOPE_CONFIG ("source_type","source_id","source_name") VALUES (:1,:2,:3)',
]

params = {'1': 1, '2': 'test_type', '3': 'gns://z', '4': 'name'}
params3 = {'1': 'test_type', '2': 'gns://z', '3': 'name'}

for i, sql in enumerate(tests):
    try:
        if len(sql.split('VALUES')) > 0:
            p = params if ':4' in sql else params3
            cur.execute(sql, p)
            cur.execute('SELECT @@IDENTITY')
            rid = cur.fetchone()[0]
            print(f'Test {i+1} OK: id={rid}')
            conn.rollback()
            break  # stop on success
    except Exception as e:
        print(f'Test {i+1} FAIL: {e}')
        conn.rollback()

conn.close()
