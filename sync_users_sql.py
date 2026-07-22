"""Sync users directly to BISHENG MySQL — bypass API password encryption issue."""
import json, uuid, sys
sys.path.insert(0, ".")
from app.models import init_db, get_session
from app.models.principal_mapping import SyncPrincipalMapping
from sqlmodel import select, func

with open("anyshare_users.json", "r", encoding="utf-8") as f:
    users = json.load(f)

# Generate SQL INSERTs for user table
print("-- Run these on the BISHENG MySQL server:")
print("-- docker exec -i bisheng-mysql mysql -uroot -p1234 bisheng")
print()

count = 0
for u in users[:100]:
    name = u["name"]
    ext_id = u["id"]
    safe_name = name.replace("'", "''")[:64]
    if not safe_name:
        safe_name = f"user_{ext_id[:8]}"

    print(f"INSERT IGNORE INTO user (user_name, password, source, external_id, token_version, create_time, update_time, password_update_time) "
          f"VALUES ('{safe_name}', '502e16c20bd629b9681e6ed13a58c02c', 'anyshare', '{ext_id}', 1, NOW(), NOW(), NOW());")

    # userrole
    print(f"INSERT IGNORE INTO userrole (user_id, role_id, tenant_id, create_time, update_time) "
          f"SELECT user_id, 2, 1, NOW(), NOW() FROM user WHERE external_id='{ext_id}';")

    # user_tenant
    print(f"INSERT IGNORE INTO user_tenant (user_id, tenant_id, is_default, status, is_active, join_time) "
          f"SELECT user_id, 1, 1, 'active', 1, NOW() FROM user WHERE external_id='{ext_id}';")

    # user_department (all to dept 1)
    print(f"INSERT IGNORE INTO user_department (user_id, department_id, is_primary, source, create_time) "
          f"SELECT user_id, 1, 1, 'anyshare', NOW() FROM user WHERE external_id='{ext_id}';")
    print()

    count += 1

print(f"-- Total: {count} users")
print(f"-- Save this output to /tmp/sync_users.sql on the BISHENG server, then:")
print(f"-- docker exec -i bisheng-mysql mysql -uroot -p1234 bisheng < /tmp/sync_users.sql")

# Also write principal mappings
init_db()
mapped = 0
for u in users[:100]:
    with get_session() as s:
        exists = s.exec(select(SyncPrincipalMapping).where(
            SyncPrincipalMapping.source_id == u["id"]
        )).first()
        if not exists:
            s.add(SyncPrincipalMapping(
                source_id=u["id"], source_type="user", source_name=u["name"],
                target_id=None, status="pending",
                match_method="sql_insert",
            ))
            s.commit()
            mapped += 1
print(f"\n-- Principal mappings written: {mapped}")
