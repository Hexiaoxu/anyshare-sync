"""Test IDENTITY auto-increment on Dameng"""
import dmPython
conn = dmPython.connect(user='SYSDBA', password='6o+%s3z2NK7J',
                        server='192.168.107.9', port=5236)
cur = conn.cursor()
cur.execute("INSERT INTO BISHENG_FOR_AISHU.ANYSHARE_SYNC_SCOPE_CONFIG (SOURCE_TYPE, SOURCE_ID, SOURCE_NAME) VALUES ('test', 'gns://x', 'test')")
cur.execute('SELECT ID, SOURCE_NAME FROM BISHENG_FOR_AISHU.ANYSHARE_SYNC_SCOPE_CONFIG')
print('IDENTITY: ', cur.fetchone())
conn.rollback()
conn.close()
