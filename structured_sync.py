"""Structured org sync: departments first, then users under departments.

Phase 1: Parse xlsx → extract unique department tree → create in BISHENG (parent→child)
Phase 2: Create users via POST /api/v1/departments/local-members

Usage:
  python structured_sync.py
"""

import base64
import json
import logging
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import httpx
import rsa as rsa_lib
from app.models import init_db, get_session
from app.models.principal_mapping import SyncPrincipalMapping
from sqlmodel import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("structured-sync")

# ── Config ────────────────────────────────────────────────
BISHENG = "http://192.168.106.161:3001"
BS_COOKIE = input("BISHENG access_token_cookie: ").strip()
BS = {"access_token_cookie": BS_COOKIE}
XLSX_PATH = Path(__file__).parent / "用户的信息.xlsx"

# ── Phase 0: Parse xlsx ───────────────────────────────────
def parse_xlsx(path):
    """Extract users with department paths from the xlsx."""
    tmp = Path("_tmp_xlsx")
    if not tmp.exists():
        import zipfile
        with zipfile.ZipFile(path, 'r') as zf:
            zf.extractall(tmp)

    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    tree = ET.parse(str(tmp / 'xl/sharedStrings.xml'))
    strings = [t.text or '' for t in tree.getroot().findall('.//s:t', ns)]

    tree2 = ET.parse(str(tmp / 'xl/worksheets/sheet1.xml'))
    rows = tree2.getroot().findall('.//s:row', ns)

    users = []
    for row in rows[3:]:  # skip header rows
        cells = {}
        for c in row.findall('s:c', ns):
            ref = c.get('r')
            v = c.find('s:v', ns)
            t = c.get('t', '')
            val = v.text if v is not None else ''
            if t == 's' and val:
                val = strings[int(val)] if int(val) < len(strings) else val

            col = ''.join(ch for ch in ref if ch.isalpha())
            cells[col] = val

        username = cells.get('A', '').strip()
        display = cells.get('B', '').strip()
        dept_path = cells.get('C', '').strip()
        phone = cells.get('G', '').strip()
        email = cells.get('F', '').strip()
        status = cells.get('L', '').strip()

        if not username or len(username) < 2:
            continue
        if '填写须知' in username or '用户的信息' in username:
            continue
        if status == '禁用':
            continue

        users.append({
            'username': username,
            'display': display or username,
            'dept_path': dept_path,
            'phone': phone,
            'email': email,
        })

    logger.info(f"Parsed {len(users)} active users from xlsx")
    return users


def build_dept_tree(users):
    """Build department tree + per-dept user counts.
    Returns (tree, dept_direct_users, dept_total_users)

    tree:                {name: {child_name: ...}}
    dept_direct_users:   {full_path: [user_dict]}  — users directly in this dept
    dept_total_users:    {full_path: int}           — users in this dept + all sub-depts
    """
    tree = {}
    dept_direct = defaultdict(list)

    for u in users:
        path = u['dept_path']
        if not path:
            continue
        parts = [p.strip() for p in path.split('/') if p.strip()]
        full_path = '/'.join(parts)
        dept_direct[full_path].append(u)

        current = tree
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]

    # Recursively compute total users per department
    dept_total = {}
    def compute_total(name, children, parent_path=""):
        full = f"{parent_path}/{name}" if parent_path else name
        total = len(dept_direct.get(full, []))
        for child_name, child_tree in sorted(children.items()):
            total += compute_total(child_name, child_tree, full)
        dept_total[full] = total
        return total

    total_users = 0
    for name, children in sorted(tree.items()):
        total_users += compute_total(name, children, "")

    def count_nodes(d):
        return 1 + sum(count_nodes(v) for v in d.values())

    logger.info(f"Department tree: {count_nodes(tree)} unique departments, {total_users} total users")
    return tree, dept_direct, dept_total


def print_tree(tree, dept_total, indent=0, parent_path=""):
    """Print department tree with user counts."""
    for name, children in sorted(tree.items()):
        full = f"{parent_path}/{name}" if parent_path else name
        user_count = dept_total.get(full, 0)
        logger.info(f"  {'  ' * indent}{name} ({user_count} users)")
        if indent < 4:
            print_tree(children, dept_total, indent + 1, full)


# ── Phase 1: Create departments top-down ─────────────────
def _parse_dept_response(data: dict) -> tuple[int | None, str]:
    """Parse BISHENG department response, handling both success and error envelopes."""
    # BISHENG wraps in: {"status_code": 200, "status_message": "SUCCESS", "data": {...}}
    # Errors also return HTTP 200: {"status_code": 422, "status_message": [...]}
    if data.get("status_code") != 200:
        logger.warning(f"  BISHENG error: status_code={data.get('status_code')}, msg={str(data.get('status_message'))[:200]}")
        return None, ""
    inner = data.get("data", {})
    if isinstance(inner, dict):
        return inner.get("id"), inner.get("dept_id", "")
    return None, ""


def create_departments(tree, parent_id, parent_path=""):
    """Recursively create departments level by level. Returns {full_path: (id, dept_id)}."""
    mapping = {}

    for name, children in sorted(tree.items()):
        full_path = f"{parent_path}/{name}" if parent_path else name

        body = {"name": name, "parent_id": parent_id}

        try:
            with httpx.Client(timeout=30) as c:
                r = c.post(f"{BISHENG}/api/v1/departments/",
                          json=body, cookies=BS)
                data = r.json()
                dept_id, dept_ext_id = _parse_dept_response(data)

                if dept_id is not None:
                    mapping[full_path] = (dept_id, dept_ext_id)
                    logger.info(f"  Created: {full_path} -> id={dept_id} ext={dept_ext_id}")
                else:
                    # Try to find existing by name
                    r2 = c.get(f"{BISHENG}/api/v1/departments/children",
                              params={"parent_id": parent_id}, cookies=BS)
                    found = False
                    if r2.status_code == 200:
                        raw_data = r2.json().get("data", [])
                        # data can be a list (v2.6.0) or dict with "children" key
                        if isinstance(raw_data, list):
                            children_list = raw_data
                        elif isinstance(raw_data, dict):
                            children_list = raw_data.get("children", raw_data.get("data", []))
                        else:
                            children_list = []
                        for ch in children_list:
                            if ch.get("name") == name:
                                dept_id = ch.get("id")
                                dept_ext_id = ch.get("dept_id", "")
                                mapping[full_path] = (dept_id, dept_ext_id)
                                logger.info(f"  Existing: {full_path} -> id={dept_id}")
                                found = True
                                break
                    if not found:
                        logger.warning(f"  SKIP: {full_path} — creation failed (parent_id={parent_id}), skipping subtree with {len(children)} sub-depts")
                        continue  # skip this dept AND its children
        except Exception as e:
            logger.error(f"  ERROR: {full_path} - {e}")
            continue

        # Only recurse if parent was successfully created/found
        if dept_id is not None:
            child_map = create_departments(children, dept_id, full_path)
            mapping.update(child_map)

    return mapping


# ── RSA password encryption ─────────────────────────────
_rsa_pubkey_cache = None

def get_encrypted_password(plain_password: str) -> str:
    """Fetch BISHENG RSA public key (cached) and encrypt password."""
    global _rsa_pubkey_cache
    if _rsa_pubkey_cache is None:
        with httpx.Client(timeout=10) as c:
            r = c.get(f"{BISHENG}/api/v1/user/public_key", cookies=BS)
            data = r.json()
            pubkey_str = data.get("data", {}).get("public_key", "")
            if not pubkey_str:
                logger.warning("No RSA public key from BISHENG, using plain text (dev mode)")
                _rsa_pubkey_cache = False   # sentinel: dev mode
            else:
                _rsa_pubkey_cache = rsa_lib.PublicKey.load_pkcs1(pubkey_str.encode())
                logger.info("RSA public key loaded (cached for session)")

    if _rsa_pubkey_cache is False:
        return plain_password
    encrypted = rsa_lib.encrypt(plain_password.encode(), _rsa_pubkey_cache)
    return base64.b64encode(encrypted).decode()
def create_users(users, dept_mapping):
    """Create users via local-members endpoint, one per department."""
    created = 0
    skipped = 0
    failed = 0
    no_dept = 0

    init_db()

    for i, u in enumerate(users):
        username = u['username']
        dept_path = u['dept_path']
        display = u['display']

        if (i + 1) % 100 == 0:
            logger.info(f"  Progress: {i+1}/{len(users)} (created={created}, skipped={skipped}, failed={failed})")

        if not dept_path:
            no_dept += 1
            continue

        # Find the department
        dept_info = dept_mapping.get(dept_path)
        if not dept_info:
            # Try fuzzy match - find longest matching parent
            parts = [p.strip() for p in dept_path.split('/') if p.strip()]
            found = False
            for j in range(len(parts) - 1, 0, -1):
                parent_path = '/'.join(parts[:j+1])
                if parent_path in dept_mapping:
                    dept_info = dept_mapping[parent_path]
                    found = True
                    break
            if not found:
                failed += 1
                if failed <= 5:
                    logger.warning(f"  No dept for: {username} -> {dept_path[:80]}")
                continue

        dept_id, dept_ext_id = dept_info

        # Check existing in DB
        with get_session() as s:
            existing = s.exec(
                select(SyncPrincipalMapping).where(
                    SyncPrincipalMapping.source_id == username
                )
            ).first()
            if existing and existing.status == "mapped":
                skipped += 1
                continue

        # Create user via local-members
        try:
            enc_pwd = get_encrypted_password("Test123456.")
            body = {
                "user_name": display,       # BISHENG显示名 = xlsx显示名
                "person_id": username,      # BISHENG人员ID = xlsx用户名
                "password": enc_pwd,
                "dept_id": dept_ext_id,
                "role_ids": [2],  # 2 = default member role
            }
            with httpx.Client(timeout=30) as c:
                r = c.post(f"{BISHENG}/api/v1/departments/local-members",
                          json=body, cookies=BS)
                data = r.json()
                sc = data.get("status_code", 0)

                if sc == 200:
                    inner = data.get("data", {})
                    bs_uid = inner.get("user_id", inner.get("id", 0))

                    if bs_uid == 0:
                        # Debug: shouldn't happen, print raw response
                        if created < 3:
                            logger.warning(f"  user_id=0, raw: {json.dumps(data, ensure_ascii=False)[:300]}")

                    with get_session() as s:
                        existing2 = s.exec(
                            select(SyncPrincipalMapping).where(
                                SyncPrincipalMapping.source_id == username
                            )
                        ).first()
                        if not existing2:
                            s.add(SyncPrincipalMapping(
                                source_id=username,
                                source_type="user",
                                source_name=display,
                                target_id=bs_uid,
                                status="mapped",
                                match_method="structured_sync",
                            ))
                            s.commit()

                    created += 1
                    if created <= 10:
                        logger.info(f"  Created: {username} -> user_id={bs_uid}, dept={dept_path[:60]}")
                else:
                    err_msg = str(data.get("status_message", ""))[:200]
                    if "Duplicate" in err_msg or "already" in err_msg.lower() or "exists" in err_msg.lower():
                        skipped += 1
                    else:
                        failed += 1
                        if failed <= 5:
                            logger.warning(f"  FAIL {username}: sc={sc} msg={err_msg}")
        except Exception as e:
            failed += 1
            if failed <= 5:
                logger.warning(f"  ERROR {username}: {str(e)[:100]}")

    logger.info(f"Users: created={created}, skipped={skipped}, failed={failed}, no_dept={no_dept}")


# ── Main ─────────────────────────────────────────────────
def main():
    logger.info("=" * 60)
    logger.info("Phase 0: Parse xlsx")
    logger.info("=" * 60)
    users = parse_xlsx(XLSX_PATH)

    logger.info("\nPhase 0.5: Build department tree + user counts")
    tree, dept_direct, dept_total = build_dept_tree(users)
    print_tree(tree, dept_total)

    # ── Load or create dept_mapping ─────────────────────
    dept_mapping = {}
    mapping_file = Path(__file__).parent / "_dept_mapping.json"
    if mapping_file.exists():
        try:
            with open(mapping_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if raw:
                dept_mapping = {k: tuple(v) for k, v in raw.items()}
                logger.info(f"Loaded {len(dept_mapping)} dept mappings from {mapping_file}")
                reuse = input("Reuse saved dept_mapping? (Y/n): ").strip().lower()
                if reuse != 'n':
                    logger.info(f"Reusing saved mapping ({len(dept_mapping)} depts), skipping Phase 1")
                else:
                    dept_mapping = {}
        except Exception as e:
            logger.warning(f"Failed to load mapping file: {e}")

    if not dept_mapping:
        logger.info(f"\nPhase 1: Create departments in BISHENG")
        logger.info("=" * 60)

        # First, check existing departments
        with httpx.Client(timeout=30) as c:
            r = c.get(f"{BISHENG}/api/v1/departments/children",
                     params={"include_archived": False}, cookies=BS)
            if r.status_code == 200:
                raw_data = r.json().get("data", [])
                if isinstance(raw_data, list):
                    root_children = raw_data
                elif isinstance(raw_data, dict):
                    root_children = raw_data.get("children", raw_data.get("data", []))
                else:
                    root_children = []
                logger.info(f"Existing root-level departments: {len(root_children)}")
                for ch in root_children:
                    logger.info(f"  id={ch.get('id')}, name={ch.get('name')}, dept_id={ch.get('dept_id')}")

        root_id_str = input("\nRoot department parent_id (paste from above, or enter existing dept id): ").strip()
        if not root_id_str:
            logger.error("No parent_id provided — aborting.")
            return
        root_id = int(root_id_str)

        dept_mapping = create_departments(tree, root_id)
        logger.info(f"\nTotal departments mapped: {len(dept_mapping)}")

        if not dept_mapping:
            logger.error("dept_mapping is EMPTY! Phase 1 failed to create or find any departments. Aborting.")
            return

        # Save mapping immediately
        with open(mapping_file, "w", encoding="utf-8") as f:
            json.dump({k: list(v) for k, v in dept_mapping.items()}, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(dept_mapping)} dept mappings to _dept_mapping.json")

    logger.info(f"\nPhase 2: Create users under departments")
    logger.info("=" * 60)

    confirm = input(f"Create {len(users)} users? (y/N): ").strip().lower()
    if confirm != 'y':
        logger.info("Skipping user creation.")
        return

    create_users(users, dept_mapping)

    logger.info("\nDone!")


if __name__ == "__main__":
    main()
