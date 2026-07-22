"""View Dameng database contents"""
import dmPython

conn = dmPython.connect(
    user='SYSDBA',
    password='6o+%s3z2NK7J',
    server='192.168.107.9',
    port=5236
)
cur = conn.cursor()

# Show all schemas
print("=== Schemas ===")
cur.execute("SELECT name FROM dba_users")
for r in cur.fetchall():
    print(f"  {r[0]}")

# Show all tables in SYSDBA
print("\n=== Tables (SYSDBA) ===")
cur.execute("""
    SELECT table_name, tablespace_name
    FROM dba_tables WHERE owner = 'SYSDBA'
    ORDER BY table_name
""")
tables = cur.fetchall()
for r in tables:
    print(f"  {r[0]:40s}  tablespace={r[1]}")
if not tables:
    print("  (no tables yet)")

# If our app tables exist, show counts
app_tables = [r[0] for r in tables]
for t in app_tables:
    try:
        cur.execute(f"SELECT COUNT(*) FROM SYSDBA.{t}")
        cnt = cur.fetchone()[0]
        print(f"  {t}: {cnt} rows")
    except:
        pass

conn.close()
