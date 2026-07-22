"""Debug: check _full_table and schema"""
from app.models.base import _full_table, _db, _db_type
print("db_type:", _db_type)
print("schema from config:", _db.get("schema"))
print("full_table result:", _full_table("anyshare_sync_scope_config"))
