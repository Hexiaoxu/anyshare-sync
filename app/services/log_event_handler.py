"""Console Log Event Handler — map AnyShare EACPLog → BISHENG actions.

Each opType from Console API is treated as an event, dispatched to the
corresponding BISHENG sync action via the SyncPipeline.
"""

from __future__ import annotations

import logging
import re
import shutil
import uuid as uuid_mod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.sync_pipeline import SyncPipeline


class EventAction(str, Enum):
    SYNC_FILE = "sync_file"
    SYNC_FOLDER = "sync_folder"
    SYNC_ACL = "sync_acl"
    DELETE = "delete"
    CREATE_USER = "create_user"
    CREATE_DEPT = "create_dept"
    UPDATE_USER_DEPT = "update_user_dept"
    IGNORE = "ignore"


@dataclass
class LogEvent:
    log_type: int
    op_type: int
    obj_id: str
    user_id: str
    user_name: str
    timestamp: int
    msg: str
    ex_msg: str

    @property
    def action(self) -> EventAction:
        return _ACTION_MAP.get((self.log_type, self.op_type), EventAction.IGNORE)


_ACTION_MAP: dict[tuple[int, int], EventAction] = {
    # Document operations
    (12, 2):  EventAction.SYNC_FILE,
    (12, 22): EventAction.SYNC_FOLDER,
    (12, 11): EventAction.SYNC_ACL,
    (12, 19): EventAction.SYNC_FILE,
    (12, 3):  EventAction.DELETE,
    (12, 24): EventAction.DELETE,
    # Organization operations
    (11, 1):  EventAction.CREATE_DEPT,
    (11, 3):  EventAction.CREATE_USER,
    (11, 6):  EventAction.UPDATE_USER_DEPT,
    (11, 7):  EventAction.UPDATE_USER_DEPT,
    # Ignored
    (10, 1):  EventAction.IGNORE,
    (10, 3):  EventAction.IGNORE,
    (11, 4):  EventAction.IGNORE,
    (11, 8):  EventAction.IGNORE,
    (12, 1):  EventAction.IGNORE,
    (12, 28): EventAction.IGNORE,
}


class LogEventHandler:
    """Dispatches console log events to BISHENG sync actions.

    Requires a SyncPipeline with populated UUID→GNS and folder/file maps.
    """

    def __init__(self, pipeline: "SyncPipeline", bs_cookie: str = ""):
        self._pipeline = pipeline
        self._bs_cookie = bs_cookie
        self._bs_base = pipeline._bs._url
        self._stats: dict[EventAction, int] = {}

    def handle(self, events: list[dict]) -> dict:
        self._stats = {a: 0 for a in EventAction}
        errors = 0
        for entry in events:
            event = self._parse(entry)
            action = event.action
            try:
                handler = _HANDLERS.get(action)
                if handler:
                    handler(self, event)
                self._stats[action] += 1
            except Exception as e:
                logger.warning(f"Handler error {action.value} {event.obj_id}: {e}")
                errors += 1
        return {"stats": {k.value: v for k, v in self._stats.items() if v > 0},
                "errors": errors}

    # ═══════════════════════════════════════════════════════════
    # Document handlers
    # ═══════════════════════════════════════════════════════════

    def _handle_acl_change(self, event: LogEvent):
        """OP11: Re-fetch ACL and re-authorize."""
        gns = self._pipeline.resolve_uuid(event.obj_id)
        if not gns:
            return
        bs_id = (self._pipeline._folder_map.get(gns) or
                 self._pipeline._file_map.get(gns))
        if not bs_id:
            return
        res_type = "folder" if gns in self._pipeline._folder_map else "knowledge_file"
        grants = self._pipeline._build_grants_for_gns(gns)
        if grants:
            self._pipeline._bs_perm.authorize(res_type, bs_id, grants=grants,
                                              timeout=60, retries=2)

    def _handle_new_file(self, event: LogEvent):
        """OP2/OP19: Download from AnyShare → upload → register → authorize."""
        gns = self._pipeline.resolve_uuid(event.obj_id)
        if not gns:
            logger.warning(f"UUID not found in mapping: {event.obj_id}")
            return

        # Extract file name from exMsg path or msg
        name = self._extract_filename(event)
        if not name:
            return

        # Find parent folder BISHENG ID
        parent_id = self._find_parent_bs_id(gns, event)

        try:
            # Download
            r = httpx.post(
                f"{self._pipeline._as_base}/api/efast/v1/file/osdownload",
                json={"docid": gns, "rev": "", "authtype": "QUERY_STRING",
                      "savename": name, "usehttps": True},
                headers={"Authorization": f"Bearer {self._pipeline._as_token}"},
                timeout=30)
            auth = r.json()["authrequest"]
            headers = {}
            for h in auth[2:]:
                if ": " in h:
                    k, v = h.split(": ", 1)
                    headers[k] = v

            safe_name = "".join(c for c in name if c.isalnum() or c in "._-()（）")
            tmp = Path.home() / "AppData" / "Local" / "Temp" / "as_sync" / uuid_mod.uuid4().hex[:8]
            tmp.mkdir(parents=True, exist_ok=True)
            local = tmp / safe_name

            with httpx.Client(timeout=120) as cc:
                with cc.stream(auth[0], auth[1], headers=headers) as rr:
                    rr.raise_for_status()
                    with open(local, "wb") as fh:
                        for chunk in rr.iter_bytes(65536):
                            fh.write(chunk)

            # Upload + Register
            fp = self._pipeline._bs_file.upload_to_minio(
                self._pipeline._space_id, local)
            reg = self._pipeline._bs_file.register(
                self._pipeline._space_id, fp, parent_id=parent_id)
            fid = reg["id"]
            self._pipeline._file_map[gns] = fid
            self._pipeline._gns_to_name[gns] = name

            # Authorize
            grants = self._pipeline._build_grants_for_gns(gns)
            if grants:
                self._pipeline._bs_perm.authorize(
                    "knowledge_file", fid, grants=grants, timeout=60, retries=2)

            local.unlink(missing_ok=True)
            shutil.rmtree(tmp, ignore_errors=True)
            logger.info(f"Synced new file: {name[:50]} -> BS id={fid}")

        except Exception as e:
            logger.error(f"Failed to sync file {event.obj_id} ({name[:40]}): {e}")

    def _handle_new_folder(self, event: LogEvent):
        """OP22: Create folder in BISHENG, matching AnyShare parent structure."""
        gns = self._pipeline.resolve_uuid(event.obj_id)
        if not gns:
            logger.warning(f"UUID not found: {event.obj_id}")
            return

        name = self._extract_filename(event)
        parent_id = self._find_parent_bs_id(gns, event)

        try:
            fid = self._pipeline._bs_folder.create(
                self._pipeline._space_id, name, parent_id=parent_id)
            self._pipeline._folder_map[gns] = fid
            self._pipeline._gns_to_name[gns] = name

            # Authorize
            grants = self._pipeline._build_grants_for_gns(gns)
            if grants:
                self._pipeline._bs_perm.authorize(
                    "folder", fid, grants=grants, timeout=60, retries=2)

            logger.info(f"Synced new folder: {name[:40]} -> BS id={fid}")
        except Exception as e:
            logger.error(f"Failed to create folder {name[:40]}: {e}")

    def _handle_delete(self, event: LogEvent):
        """OP3/OP24: Mark as deleted in mapping table."""
        gns = self._pipeline.resolve_uuid(event.obj_id)
        if not gns:
            return
        from app.models import get_session
        from app.models.document_mapping import SyncDocumentMapping
        from sqlmodel import select
        with get_session() as s:
            dm = s.exec(select(SyncDocumentMapping).where(
                SyncDocumentMapping.source_doc_id == gns)).first()
            if dm:
                dm.status = "deleted"
                s.commit()
                logger.info(f"Marked deleted: {dm.source_name}")

    # ═══════════════════════════════════════════════════════════
    # Organization handlers
    # ═══════════════════════════════════════════════════════════

    def _handle_create_user(self, event: LogEvent):
        """OP3 in LT11: Create/update user in BISHENG.

        Parses msg like: '修改 用户5jWangzhi(王志1)成功'
        or:              '新建 用户xxx(yyy)成功'
        """
        username, display_name = self._parse_user_from_msg(event.msg)
        if not username:
            return

        try:
            # Check if user already exists
            r = httpx.get(
                f"{self._bs_base}/api/v1/permissions/resources/"
                f"knowledge_space/{self._pipeline._space_id}/grant-subjects/users",
                params={"keyword": username, "page": 1, "page_size": 5},
                cookies={"access_token_cookie": self._bs_cookie}, timeout=15)
            for u in r.json().get("data", []):
                if u.get("external_id") == username or u.get("user_name") == display_name:
                    logger.debug(f"User already exists: {username}")
                    return

            # Create user via API
            r2 = httpx.post(
                f"{self._bs_base}/api/v1/user",
                json={"user_name": display_name or username,
                      "external_id": username,
                      "password": "Sync@123456"},
                cookies={"access_token_cookie": self._bs_cookie}, timeout=15)
            if r2.status_code == 200:
                logger.info(f"Created user: {username}")
            else:
                logger.warning(f"Create user failed: {r2.text[:100]}")
        except Exception as e:
            logger.warning(f"Create user error {username}: {e}")

    def _handle_create_dept(self, event: LogEvent):
        """OP1 in LT11: Create department in BISHENG.

        Parses msg like: '新建 部门安全环保办公成成功'
        """
        dept_name = self._parse_dept_from_msg(event.msg)
        if not dept_name:
            return

        try:
            # Check if department exists
            r = httpx.get(
                f"{self._bs_base}/api/v1/permissions/resources/"
                f"knowledge_space/{self._pipeline._space_id}"
                f"/grant-subjects/departments/search",
                params={"keyword": dept_name, "limit": 5},
                cookies={"access_token_cookie": self._bs_cookie}, timeout=15)
            for root in r.json().get("data", {}).get("roots", []):
                if _find_dept_in_tree([root], dept_name):
                    logger.debug(f"Dept already exists: {dept_name}")
                    return

            # Create department
            r2 = httpx.post(
                f"{self._bs_base}/api/v1/department",
                json={"name": dept_name, "parent_id": 1},
                cookies={"access_token_cookie": self._bs_cookie}, timeout=15)
            if r2.status_code == 200:
                logger.info(f"Created department: {dept_name}")
            else:
                logger.warning(f"Create dept failed: {r2.text[:100]}")
        except Exception as e:
            logger.warning(f"Create dept error {dept_name}: {e}")

    def _handle_user_dept_change(self, event: LogEvent):
        """OP6/OP7: User moved to/from department.
        Limited action: log the change. Full sync would require
        Console API Usrm_GetSubUsers for precise department membership.
        """
        # Extract info from msg like: '移动 用户5jWangzhi从部门X到部门Y成功'
        logger.info(f"User dept change: {event.msg[:100]}")
        # For now: the user's permissions will be re-synced when
        # OP11 (permission change) events fire for affected documents.

    # ═══════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _parse(entry: dict) -> LogEvent:
        return LogEvent(
            log_type=entry.get("logType", 0),
            op_type=entry.get("opType", 0),
            obj_id=entry.get("objId", ""),
            user_id=entry.get("userId", ""),
            user_name=entry.get("userName", ""),
            timestamp=entry.get("date", 0),
            msg=entry.get("msg", ""),
            ex_msg=entry.get("exMsg", ""),
        )

    @staticmethod
    def _extract_filename(event: LogEvent) -> str:
        """Extract filename from exMsg path: 'AnyShare://.../xxx.docx' -> 'xxx.docx'"""
        ex = event.ex_msg
        if "文件路径:" in ex and ".doc" in ex:
            m = re.search(r'AnyShare://.*?/([^/]+?\.[a-zA-Z0-9]+)', ex)
            if m:
                return m.group(1)
        # Fallback: try msg
        m2 = re.search(r'[《]([^《》]+)[》]', event.msg)
        if m2:
            return m2.group(1)
        return ""

    def _find_parent_bs_id(self, gns: str, event: LogEvent) -> int | None:
        """Find BISHENG folder ID for the parent of this GNS."""
        # Try direct parent GNS
        parent_gns = gns.rsplit("/", 1)[0] if "/" in gns else ""
        if parent_gns in self._pipeline._folder_map:
            return self._pipeline._folder_map[parent_gns]
        # Try ancestor path from exMsg
        return None

    @staticmethod
    def _parse_user_from_msg(msg: str) -> tuple[str, str]:
        """Parse username and display name from msg.
        '修改 用户5jWangzhi(王志1)成功' -> ('5jWangzhi', '王志1')
        '新建 用户zhangsan(张三)成功' -> ('zhangsan', '张三')
        """
        m = re.search(r'用户\s*(\S+?)\((\S+?)\)', msg)
        if m:
            return m.group(1), m.group(2)
        return "", ""

    @staticmethod
    def _parse_dept_from_msg(msg: str) -> str:
        """Parse department name from msg.
        '新建 部门安全环保办公成成功' -> not reliable, skip
        """
        m = re.search(r'部门(\S+?)成功', msg)
        if m:
            return m.group(1)
        return ""


# ── Handler dispatch table ──────────────────────────────────

_HANDLERS = {
    EventAction.SYNC_ACL: LogEventHandler._handle_acl_change,
    EventAction.SYNC_FILE: LogEventHandler._handle_new_file,
    EventAction.SYNC_FOLDER: LogEventHandler._handle_new_folder,
    EventAction.DELETE: LogEventHandler._handle_delete,
    EventAction.CREATE_USER: LogEventHandler._handle_create_user,
    EventAction.CREATE_DEPT: LogEventHandler._handle_create_dept,
    EventAction.UPDATE_USER_DEPT: LogEventHandler._handle_user_dept_change,
}


def _find_dept_in_tree(nodes: list[dict], target: str) -> bool:
    """Recursively search department tree for exact name match."""
    for n in nodes:
        if n.get("name") == target:
            return True
        if n.get("children") and _find_dept_in_tree(n["children"], target):
            return True
    return False
