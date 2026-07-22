"""Test Dameng database connection"""
import dmPython

try:
    conn = dmPython.connect(
        user='SYSDBA',
        password='6o+%s3z2NK7J',
        server='192.168.107.9',
        port=5236
    )
    cur = conn.cursor()
    cur.execute('SELECT 1 FROM DUAL')
    print('Connected:', cur.fetchone())
    cur.execute("SELECT name FROM v$database")
    print('Database:', cur.fetchone())
    conn.close()
    print('>>> Dameng connection OK! <<<')
except Exception as e:
    print(f'Error: {e}')
