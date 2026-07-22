"""Test SQLAlchemy + Dameng with table creation"""
from sqlmodel import SQLModel, Field, Session, create_engine
from typing import Optional
import urllib.parse

# Password has special chars, need to URL-encode for connection string
user = 'SYSDBA'
pwd = urllib.parse.quote_plus('6o+%s3z2NK7J')
host = '192.168.107.9'
port = 5236

# Try dmPython dialect
conn_url = f"dm+dmPython://{user}:{pwd}@{host}:{port}"

print(f"Connecting to: dm+dmPython://{user}:***@{host}:{port}")

try:
    engine = create_engine(conn_url, echo=True)
    conn = engine.connect()
    result = conn.exec_driver_sql("SELECT 1 FROM DUAL")
    print(f"Connected: {result.fetchone()}")
    conn.close()
    print(">>> SQLAlchemy connection OK! <<<")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
    print("\nTrying without dialect prefix...")
    try:
        engine2 = create_engine(f"dm://{user}:{pwd}@{host}:{port}", echo=True)
        conn2 = engine2.connect()
        print("Connected via dm:// prefix")
        conn2.close()
    except Exception as e2:
        print(f"Error: {e2}")
