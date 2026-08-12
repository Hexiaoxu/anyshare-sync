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
    (12, 2):  EventAction.SYNC_FILE,    # 上传/创建文件
    (12, 4):  EventAction.SYNC_FILE,    # 秒传修改文件
    (12, 11): EventAction.SYNC_ACL,     # 权限变更
    (12, 19): EventAction.SYNC_FILE,    # 重命名（重新同步）
    (12, 22): EventAction.SYNC_FOLDER,  # 新建文件夹
    (12, 24): EventAction.SYNC_FILE,    # 复制文件
    # Organization operations
    (11, 1):  EventAction.CREATE_DEPT,      # 新建部门/添加用户到部门
    (11, 3):  EventAction.CREATE_USER,      # 创建/覆盖用户
    (11, 6):  EventAction.UPDATE_USER_DEPT, # 移动用户到部门
    (11, 7):  EventAction.UPDATE_USER_DEPT, # 从部门移除用户
    (11, 8):  EventAction.CREATE_USER,      # 从外部系统同步用户
    # Ignored
    (10, 1):  EventAction.IGNORE,   # 登录
    (10, 3):  EventAction.IGNORE,   # 登录认证
    (11, 4):  EventAction.IGNORE,   # 修改用户信息
    (11, 9):  EventAction.IGNORE,   # 导出日志
    (12, 1):  EventAction.IGNORE,   # 预览
    (12, 3):  EventAction.IGNORE,   # 下载（非删除）
    (12, 28): EventAction.IGNORE,   # 触发任务
    (12, 33): EventAction.IGNORE,   # 预览问答
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
        # Try GNS from UUID map first
        gns = self._pipeline.resolve_uuid(event.obj_id)

        # Fallback: construct GNS from exMsg parent path + UUID
        if not gns:
            gns = self._resolve_gns_from_ex_msg(event)

        # Extract file name from exMsg or msg
        name = self._extract_filename(event)
        if not name:
            logger.warning(f"Cannot extract filename from event: {event.msg[:80]}")
            return

        # Find parent folder BISHENG ID
        parent_id = self._find_parent_bs_id(gns or "", event)

        # Resolve space_id from GNS or exMsg
        space_id = self._pipeline._space_id
        if not space_id and gns:
            space_id = self._resolve_space_id_from_gns(gns)
        if not space_id:
            logger.warning(f"Cannot resolve space_id for {name}, skipping")
            return

        # docid for download
        docid = gns if gns else event.obj_id

        try:
            # Download
            r = httpx.post(
                f"{self._pipeline._as_base}/api/efast/v1/file/osdownload",
                json={"docid": docid, "rev": "", "authtype": "QUERY_STRING",
                      "savename": name, "usehttps": True},
                headers={"Authorization": f"Bearer {self._pipeline._as_token}"},
                timeout=30)
            auth_req = r.json().get("authrequest")
            if not auth_req:
                logger.warning(f"No authrequest for {docid}: {r.text[:100]}")
                return
            headers = {}
            for h in auth_req[2:]:
                if ": " in h:
                    k, v = h.split(": ", 1)
                    headers[k] = v

            safe_name = "".join(c for c in name if c.isalnum() or c in "._-()（）")
            tmp = Path.home() / "AppData" / "Local" / "Temp" / "as_sync" / uuid_mod.uuid4().hex[:8]
            tmp.mkdir(parents=True, exist_ok=True)
            local = tmp / safe_name

            with httpx.Client(timeout=120) as cc:
                with cc.stream(auth_req[0], auth_req[1], headers=headers) as rr:
                    rr.raise_for_status()
                    with open(local, "wb") as fh:
                        for chunk in rr.iter_bytes(65536):
                            fh.write(chunk)

            # Upload + Register
            fp = self._pipeline._bs_file.upload_to_minio(space_id, local)
            reg = self._pipeline._bs_file.register(space_id, fp, parent_id=parent_id)
            fid = reg["id"]
            if gns:
                self._pipeline._file_map[gns] = fid
                self._pipeline._gns_to_name[gns] = name

            # Authorize
            if gns:
                grants = self._pipeline._build_grants_for_gns(gns)
                if grants:
                    self._pipeline._bs_perm.authorize(
                        "knowledge_file", fid, grants=grants, timeout=60, retries=2)

            local.unlink(missing_ok=True)
            shutil.rmtree(tmp, ignore_errors=True)
            logger.info(f"Synced new file: {name[:50]} -> BS id={fid} parent={parent_id}")

        except Exception as e:
            logger.error(f"Failed to sync file {docid} ({name[:40]}): {e}")

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
        """OP3/OP24: Delete file/folder from BISHENG and mark DB as deleted."""
        gns = self._pipeline.resolve_uuid(event.obj_id)
        if not gns:
            return

        is_folder = gns in self._pipeline._folder_map
        bs_id = (self._pipeline._folder_map.get(gns)
                 if is_folder else self._pipeline._file_map.get(gns))

        if bs_id:
            try:
                if is_folder:
                    self._pipeline._bs_folder.delete(self._pipeline._space_id, bs_id)
                    del self._pipeline._folder_map[gns]
                else:
                    self._pipeline._bs_file.delete_file(self._pipeline._space_id, bs_id)
                    del self._pipeline._file_map[gns]
                logger.info(f"Deleted {'folder' if is_folder else 'file'} bs_id={bs_id} gns={gns[-20:]}")
            except Exception as e:
                logger.warning(f"BISHENG delete failed bs_id={bs_id}: {e}")

        from app.models import get_session
        from app.models.document_mapping import SyncDocumentMapping
        from sqlmodel import select
        with get_session() as s:
            dm = s.exec(select(SyncDocumentMapping).where(
                SyncDocumentMapping.source_doc_id == gns)).first()
            if dm:
                dm.status = "deleted"
                s.commit()
                logger.info(f"Marked deleted in DB: {dm.source_name}")

    # ═══════════════════════════════════════════════════════════
    # Organization handlers
    # ═══════════════════════════════════════════════════════════

    def _handle_create_user(self, event: LogEvent):
        """OP3/OP8 in LT11: Create/update user in BISHENG."""
        username, display_name = self._parse_user_from_msg(event.msg)
        if not username:
            return

        try:
            # Check if user already exists via user list API
            r = httpx.get(
                f"{self._bs_base}/api/v1/user/list",
                params={"keyword": username, "page": 1, "page_size": 5},
                cookies={"access_token_cookie": self._bs_cookie}, timeout=15)
            for u in r.json().get("data", {}).get("data", []):
                if u.get("external_id") == username or u.get("user_name") == display_name:
                    logger.debug(f"User already exists: {username}")
                    return

            # Create user via regist API (same as import_org.py)
            r2 = httpx.post(
                f"{self._bs_base}/api/v1/user/regist",
                json={"user_name": display_name or username,
                      "user_id": username,
                      "password": "Sync@123456",
                      "source": "local"},
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
            # Check if department exists via departments/children API
            r = httpx.get(
                f"{self._bs_base}/api/v1/departments/children",
                params={"parent_id": 1, "include_archived": "false"},
                cookies={"access_token_cookie": self._bs_cookie}, timeout=15)
            children = r.json().get("data", {}).get("children", [])
            if any(d.get("name") == dept_name for d in children):
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
        """OP6/OP7: User moved to/from department — update department membership in BISHENG.

        msg examples:
          '移动 用户5jWangzhi(王志1)从部门X到部门Y成功'
          '移除 用户5jWangzhi(王志1)从部门X成功'
        """
        username, display_name = self._parse_user_from_msg(event.msg)
        if not username:
            logger.info(f"User dept change (unresolved): {event.msg[:100]}")
            return

        # Parse target department from msg: 到部门Y
        target_dept = ""
        m = re.search(r'到部门(\S+?)成功', event.msg)
        if m:
            target_dept = m.group(1)

        try:
            # Find user in BISHENG by external_id or display_name
            r = httpx.get(
                f"{self._bs_base}/api/v1/user/list",
                params={"keyword": display_name or username, "page": 1, "page_size": 10},
                cookies={"access_token_cookie": self._bs_cookie}, timeout=15)
            users = r.json().get("data", {}).get("data", [])
            bs_user = next(
                (u for u in users if u.get("external_id") == username
                 or u.get("user_name") == display_name),
                None)
            if not bs_user:
                # 再用 username 搜一次
                r2 = httpx.get(
                    f"{self._bs_base}/api/v1/user/list",
                    params={"keyword": username, "page": 1, "page_size": 5},
                    cookies={"access_token_cookie": self._bs_cookie}, timeout=15)
                users2 = r2.json().get("data", {}).get("data", [])
                bs_user = next(
                    (u for u in users2 if u.get("external_id") == username), None)
            if not bs_user:
                logger.warning(f"User dept change: user not found in BISHENG: {username}")
                return
            user_id = bs_user["user_id"]

            if not target_dept:
                # OP7: remove from department — no target dept, just log
                logger.info(f"User {username} removed from dept (no re-assign needed)")
                return

            # Find target department ID in BISHENG
            r2 = httpx.get(
                f"{self._bs_base}/api/v1/departments/children",
                params={"parent_id": 1, "include_archived": "false"},
                cookies={"access_token_cookie": self._bs_cookie}, timeout=15)
            dept_id = None
            for d in r2.json().get("data", {}).get("children", []):
                if d.get("name") == target_dept:
                    dept_id = d.get("id")
                    break
                if found:
                    dept_id = found
                    break

            if not dept_id:
                logger.warning(f"User dept change: dept not found in BISHENG: {target_dept}")
                return

            # Update user's department
            r3 = httpx.put(
                f"{self._bs_base}/api/v1/user/{user_id}",
                json={"department_id": dept_id},
                cookies={"access_token_cookie": self._bs_cookie}, timeout=15)
            if r3.status_code == 200:
                logger.info(f"User {username} moved to dept {target_dept} (id={dept_id})")
            else:
                logger.warning(f"User dept update failed: {r3.text[:100]}")

        except Exception as e:
            logger.warning(f"User dept change error {username}: {e}")

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
        """Extract filename from msg or exMsg."""
        msg = event.msg
        ex  = event.ex_msg

        # 1. exMsg 里的文件路径: AnyShare://.../xxx.docx
        m = re.search(r'AnyShare://.*?/([^/;]+\.[a-zA-Z0-9]+)', ex)
        if m:
            return m.group(1).strip()

        # 2. msg 里的双引号: 上传文件"xxx.docx"成功
        m = re.search(r'["“「]([^"”」]+\.[a-zA-Z0-9]+)["”」]', msg)
        if m:
            return m.group(1).strip()

        # 3. msg 里的书名号: 《xxx.docx》
        m = re.search(r'[《]([^《》]+\.[a-zA-Z0-9]+)[》]', msg)
        if m:
            return m.group(1).strip()

        # 4. exMsg 里文档名称字段
        m = re.search(r'文档名称[:：]\s*([^\s;，]+\.[a-zA-Z0-9]+)', ex)
        if m:
            return m.group(1).strip()

        return ""

    def _find_parent_bs_id(self, gns: str, event: LogEvent) -> int | None:
        """Find BISHENG folder ID for the parent of this GNS."""
        # Try direct parent GNS from folder_map
        parent_gns = gns.rsplit("/", 1)[0] if "/" in gns else ""
        if parent_gns and parent_gns in self._pipeline._folder_map:
            return self._pipeline._folder_map[parent_gns]

        # Fallback: parse parent path from exMsg
        # exMsg format: "...父路径: AnyShare://组织文档库/.../OA收文/filename; ..."
        ex = event.ex_msg
        m = re.search(r'父路径[:：]\s*AnyShare://[^/]+/(.+?)(?:;|$)', ex)
        if not m:
            # Try path without semicolon terminator
            m = re.search(r'AnyShare://[^/]+/(.+)', ex)
        if m:
            raw_path = m.group(1).strip()
            # Remove filename at the end if present (path ends with file extension)
            # The path may or may not include the filename
            parts = raw_path.split('/')
            # Try progressively shorter paths to find a match
            for length in range(len(parts), 0, -1):
                candidate = '/'.join(parts[:length])
                fid = self._pipeline._bs_folder_by_path.get(candidate)
                if fid:
                    return fid
        return None

    def _resolve_space_id_from_gns(self, gns: str) -> int | None:
        """Find BISHENG space_id for a given file/folder GNS via DB mapping."""
        try:
            from app.models import get_session
            from app.models.space_mapping import SyncSpaceMapping
            from sqlmodel import select
            # Try progressively shorter GNS prefixes to find the lib root
            parts = gns.split('/')
            for length in range(len(parts), 1, -1):
                candidate = '/'.join(parts[:length])
                with get_session() as s:
                    sm = s.exec(select(SyncSpaceMapping).where(
                        SyncSpaceMapping.source_doc_lib_id == candidate)).first()
                    if sm:
                        return sm.target_space_id
        except Exception as e:
            logger.warning(f"resolve_space_id error: {e}")
        return None

    def _resolve_gns_from_ex_msg(self, event: LogEvent) -> str | None:
        """Construct file GNS from exMsg parent path + objId UUID.

        exMsg format: '...父路径: AnyShare://系统测试用户; ...'
        Strategy: find known lib GNS from SyncSpaceMapping by name, append UUID.
        """
        ex = event.ex_msg
        m = re.search(r'父路径[:：]\s*AnyShare://([^;]+)', ex)
        if not m:
            return None

        raw_path = m.group(1).strip()
        path_parts = [p.strip() for p in raw_path.split('/') if p.strip()]

        # For personal lib: path is just the lib name e.g. "系统测试用户"
        # For dept/knowledge lib: path starts with lib name e.g. "组织文档库/公司/部门/..."
        lib_name = path_parts[0] if path_parts else ""
        try:
            from app.models import get_session
            from app.models.space_mapping import SyncSpaceMapping
            from sqlmodel import select
            with get_session() as s:
                sm = s.exec(select(SyncSpaceMapping).where(
                    SyncSpaceMapping.source_doc_lib_name == lib_name)).first()
                if sm:
                    parent_gns = sm.source_doc_lib_id
                    # If there are sub-folders in the path, append them
                    # (but we don't have their GNS, so just use lib root)
                    return f"{parent_gns}/{event.obj_id}"
        except Exception as e:
            logger.warning(f"resolve_gns_from_ex_msg error: {e}")

        logger.warning(f"Cannot resolve parent GNS from path: {raw_path}")
        return None
        """Construct file GNS from exMsg parent path + objId UUID.

        exMsg format: '...父路径: AnyShare://系统测试用户; ...'
        or:           '...父路径: AnyShare://组织文档库/.../OA收文/filename; ...'

        Strategy: find known GNS for the parent lib/folder, append UUID.
        """
        ex = event.ex_msg
        m = re.search(r'父路径[:：]\s*AnyShare://([^;]+)', ex)
        if not m:
            return None

        raw_path = m.group(1).strip()
        # raw_path examples:
        #   "系统测试用户"                          -> personal lib root
        #   "组织文档库/公司/部门/OA收文/filename"  -> dept lib subfolder

        # Check pipeline's path map (built during full sync)
        path_map = getattr(self._pipeline, '_bs_folder_by_path', {})

        # Try progressively longer paths from the known GNS roots
        # We need the parent GNS — look in pipeline's known space/folder maps
        # Approach: ask AnyShare for the parent folder GNS by path
        # For now: look through pipeline's _folder_map keys for a match
        folder_map = getattr(self._pipeline, '_folder_map', {})

        # Try to find parent GNS by matching the last path component name
        path_parts = [p.strip() for p in raw_path.split('/') if p.strip()]
        parent_name = path_parts[-1] if path_parts else ""

        # Search for a GNS whose name matches the last path component
        gns_to_name = getattr(self._pipeline, '_gns_to_name', {})
        parent_gns = next(
            (gns for gns, name in gns_to_name.items()
             if name == parent_name and gns in folder_map),
            None
        )

        # Fallback: if parent is a lib root (single path component like "系统测试用户")
        # look for a space whose name matches
        if not parent_gns and len(path_parts) == 1:
            # personal lib: parent path IS the lib name
            # find the lib GNS from space_mapping table
            try:
                from app.models import get_session
                from app.models.space_mapping import SyncSpaceMapping
                from sqlmodel import select
                with get_session() as s:
                    sm = s.exec(select(SyncSpaceMapping).where(
                        SyncSpaceMapping.source_doc_lib_name == path_parts[0])).first()
                    if sm:
                        parent_gns = sm.source_doc_lib_id
            except Exception:
                pass

        if parent_gns:
            return f"{parent_gns}/{event.obj_id}"

        logger.warning(f"Cannot resolve parent GNS from path: {raw_path}")
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


def _find_dept_node(nodes: list[dict], target: str) -> int | None:
    """Recursively search department tree, return id of matching node."""
    for n in nodes:
        if n.get("name") == target:
            return n.get("id")
        if n.get("children"):
            found = _find_dept_node(n["children"], target)
            if found:
                return found
    return None
