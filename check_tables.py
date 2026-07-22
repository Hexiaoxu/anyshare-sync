"""Check BISHENG_FOR_AISHU tables"""
import dmPython
conn=dmPython.connect(user='SYSDBA',password='6o+%s3z2NK7J',server='192.168.107.9',port=5236)
cur=conn.cursor()
cur.execute("SELECT table_name FROM dba_tables WHERE owner='BISHENG_FOR_AISHU'")
tables = [r[0] for r in cur.fetchall()]
print("Tables:", tables)
conn.close()
