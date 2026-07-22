"""Test dmPython cursor.lastrowid"""
import dmPython
conn = dmPython.connect(user='SYSDBA', password='6o+%s3z2NK7J',
                        server='192.168.107.9', port=5236)
cur = conn.cursor()
cur.execute("INSERT INTO BISHENG_FOR_AISHU.ANYSHARE_SYNC_SCOPE_CONFIG (SOURCE_TYPE, SOURCE_ID, SOURCE_NAME) VALUES ('test2', 'gns://y', 'test2')")
print('lastrowid:', repr(cur.lastrowid))
print('rowcount:', cur.rowcount)

# Alternative: SELECT @@IDENTITY
cur.execute("SELECT @@IDENTITY")
print('@@IDENTITY:', cur.fetchone())

conn.rollback()
conn.close()
