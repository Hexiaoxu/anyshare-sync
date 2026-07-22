"""Check if ID column has IDENTITY in BISHENG_FOR_AISHU"""
import dmPython
conn=dmPython.connect(user='SYSDBA',password='6o+%s3z2NK7J',server='192.168.107.9',port=5236)
cur=conn.cursor()
cur.execute("""
    SELECT COLUMN_NAME, DATA_TYPE, DATA_DEFAULT
    FROM dba_tab_columns
    WHERE owner='BISHENG_FOR_AISHU'
    AND table_name='ANYSHARE_SYNC_SCOPE_CONFIG'
    ORDER BY COLUMN_ID
""")
for r in cur.fetchall():
    print(r)
conn.close()
