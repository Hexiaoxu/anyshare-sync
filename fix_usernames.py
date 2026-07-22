"""Fix ~100 users created with username as user_name — swap to display name.

Usage: python fix_usernames.py
"""

import json
import logging
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("fix-names")

BISHENG = "http://192.168.106.161:3001"
BS_COOKIE = input("BISHENG access_token_cookie: ").strip()
BS = {"access_token_cookie": BS_COOKIE}
XLSX_PATH = Path(__file__).parent / "用户的信息.xlsx"


def parse_xlsx_users(path):
    """Parse xlsx, return {username: display_name}."""
    tmp = Path("_tmp_xlsx")
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    tree = ET.parse(str(tmp / 'xl/sharedStrings.xml'))
    strings = [t.text or '' for t in tree.getroot().findall('.//s:t', ns)]
    tree2 = ET.parse(str(tmp / 'xl/worksheets/sheet1.xml'))
    rows = tree2.getroot().findall('.//s:row', ns)

    name_map = {}
    for row in rows[3:]:
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
        username = (cells.get('A', '') or '').strip()
        display = (cells.get('B', '') or '').strip()
        if username and username not in name_map:
            name_map[username] = display or username
    return name_map


def get_wrong_users():
    """Find BISHENG users whose user_name matches a xlsx username pattern."""
    # Get users from principal_mapping (our tracking)
    try:
        from app.models import init_db, get_session
        from app.models.principal_mapping import SyncPrincipalMapping
        from sqlmodel import select
        init_db()
        with get_session() as s:
            rows = s.exec(select(SyncPrincipalMapping).where(
                SyncPrincipalMapping.status == "mapped"
            )).all()
        return {r.source_id: (r.target_id, r.source_name) for r in rows}
    except Exception as e:
        logger.warning(f"DB read failed: {e}")
        return {}


def main():
    name_map = parse_xlsx_users(XLSX_PATH)
    logger.info(f"Loaded {len(name_map)} users from xlsx")

    db_users = get_wrong_users()
    logger.info(f"Loaded {len(db_users)} mapped users from DB")

    # Find users to fix: where source_id (username) != source_name (display) — meaning old mapping
    # Actually, ALL users with the old mapping have source_name = source_id = username
    # New users after the fix have source_name = display name (different from username)
    to_fix = []
    for username, (bs_uid, source_name) in db_users.items():
        correct_display = name_map.get(username, username)
        if source_name == username and correct_display != username:
            to_fix.append((username, bs_uid, correct_display))

    logger.info(f"Users with wrong user_name: {len(to_fix)}")
    if not to_fix:
        logger.info("Nothing to fix!")
        return

    # Show samples
    for username, bs_uid, display in to_fix[:10]:
        logger.info(f"  {username} (bs_uid={bs_uid}) -> should be '{display}'")

    confirm = input(f"\nFix {len(to_fix)} users? (y/N): ").strip().lower()
    if confirm != 'y':
        return

    # Fix each: POST /api/v1/departments/{dept_id}/members/{user_id}/apply-edit
    # We need the dept_id for each user. Get from GET /api/v1/user/list or use
    # direct DB update approach. Actually, the apply-edit endpoint needs dept_id.
    #
    # Alternatively, just use GET to find each user's department first.
    #
    # Simplest: Since all users are in depts we just created, use the dept_mapping
    # to find their dept_ext_id.

    # Load dept mapping
    mapping_file = Path(__file__).parent / "_dept_mapping.json"
    if not mapping_file.exists():
        logger.error("_dept_mapping.json not found")
        return
    with open(mapping_file, "r", encoding="utf-8") as f:
        dept_mapping = json.load(f)

    # Parse xlsx again to get username → dept_path
    dept_of_user = {}
    tmp = Path("_tmp_xlsx")
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    tree = ET.parse(str(tmp / 'xl/sharedStrings.xml'))
    strings = [t.text or '' for t in tree.getroot().findall('.//s:t', ns)]
    tree2 = ET.parse(str(tmp / 'xl/worksheets/sheet1.xml'))
    rows = tree2.getroot().findall('.//s:row', ns)
    for row in rows[3:]:
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
        username = (cells.get('A', '') or '').strip()
        dept_path = (cells.get('C', '') or '').strip()
        if username and username not in dept_of_user:
            dept_of_user[username] = dept_path

    fixed = 0
    failed = 0
    for username, bs_uid, correct_display in to_fix:
        dept_path = dept_of_user.get(username, "")
        dept_ext_id = ""
        if dept_path and dept_path in dept_mapping:
            dept_ext_id = dept_mapping[dept_path][1]  # index 1 = ext_id

        if not dept_ext_id:
            logger.warning(f"  No dept for {username}")
            failed += 1
            continue

        try:
            body = {"user_name": correct_display}
            with httpx.Client(timeout=30) as c:
                r = c.post(
                    f"{BISHENG}/api/v1/departments/{dept_ext_id}/members/{bs_uid}/apply-edit",
                    json=body, cookies=BS,
                )
                data = r.json()
                if data.get("status_code") == 200:
                    fixed += 1
                    if fixed <= 10:
                        logger.info(f"  Fixed: {username} -> '{correct_display}' (uid={bs_uid})")
                else:
                    failed += 1
                    if failed <= 5:
                        logger.warning(f"  FAIL {username}: {data.get('status_message', '')[:100]}")
        except Exception as e:
            failed += 1
            if failed <= 5:
                logger.warning(f"  ERROR {username}: {e}")

    logger.info(f"\nDone: fixed={fixed}, failed={failed}")


if __name__ == "__main__":
    main()
