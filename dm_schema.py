"""Show Dameng database structure — tables, columns, row counts"""
import dmPython

conn = dmPython.connect(
    user='SYSDBA', password='6o+%s3z2NK7J',
    server='192.168.107.9', port=5236)
cur = conn.cursor()

# Show all schemas
schemas = ['SYSDBA', 'BISHENG_FOR_AISHU', 'BISHENG_AISHU']
for schema in schemas:
    # Get tables
    try:
        cur.execute(f"""
            SELECT table_name FROM dba_tables
            WHERE owner='{schema}' AND table_name NOT LIKE '##%'
            ORDER BY table_name
        """)
        tables = [r[0] for r in cur.fetchall()]
        if tables:
            print(f"\n{'='*60}")
            print(f"Schema: {schema} ({len(tables)} tables)")
            print(f"{'='*60}")
            for t in tables:
                cur.execute(f"SELECT COUNT(*) FROM {schema}.\"{t}\"")
                cnt = cur.fetchone()[0]
                print(f"\n  {t}  ({cnt} rows)")
                # Show columns
                try:
                    cur.execute(f"""
                        SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, NULLABLE
                        FROM dba_tab_columns
                        WHERE owner='{schema}' AND table_name='{t}'
                        ORDER BY COLUMN_ID
                    """)
                    for col in cur.fetchall():
                        print(f"    {col[0]:30s} {col[1]:10s} {col[2] or '':>5} {'NULL' if col[3]=='Y' else 'NOT NULL'}")
                except Exception as e:
                    print(f"    (columns error: {e})")
        else:
            print(f"\nSchema {schema}: no tables")
    except Exception as e:
        print(f"\nSchema {schema}: {e}")

conn.close()
