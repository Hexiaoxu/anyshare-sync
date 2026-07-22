"""Show Dameng tables and row counts"""
import dmPython

conn = dmPython.connect(
    user='SYSDBA', password='6o+%s3z2NK7J',
    server='192.168.107.9', port=5236)
cur = conn.cursor()

# List schemas
print("=== Users / Schemas ===")
cur.execute("SELECT username FROM dba_users WHERE account_status='OPEN'")
for r in cur.fetchall():
    print(f"  {r[0]}")

# List tables for SYSDBA
print("\n=== Tables owned by SYSDBA ===")
cur.execute("""
    SELECT owner, table_name, tablespace_name
    FROM dba_tables WHERE owner='SYSDBA'
    ORDER BY table_name
""")
tables = cur.fetchall()
for r in tables:
    print(f"  {r[0]}.{r[1]}")
if not tables:
    print("  (no tables found — init_dameng.py may have failed)")

# Check any tables with 'anyshare' or 'sync' in name
print("\n=== Any matching tables (all schemas) ===")
try:
    cur.execute("""
        SELECT owner, table_name FROM dba_tables
        WHERE lower(table_name) LIKE '%anyshare%' OR lower(table_name) LIKE '%sync%'
    """)
    for r in cur.fetchall():
        print(f"  {r[0]}.{r[1]}")
    if not cur.fetchall() and not tables:
        print("  (none)")
except:
    pass

conn.close()
