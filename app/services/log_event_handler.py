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

from app.services.path_resolver import (
    PathResolver, extract_parent_path, extract_object_path, _split_path,
)
from app.sync_pipeline import SKIP_EXTENSIONS

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.sync_pipeline import SyncPipeline

# 部门文档库的「空间边界」深度：组织文档库/公司/公司总部/部门 共 4 层。
# 与 config.trees 中部门库 4 层 GNS 及 sync_dept_lib.py 的「部门=独立空间」约定一致。
_DEPT_SPACE_DEPTH = 4


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


@dataclass
class ResolvedTarget:
    """路径/名字驱动解析出的目标对象及其 BISHENG 落地上下文。"""
    gns: str                 # 目标对象完整 GNS
    parent_gns: str          # 父目录完整 GNS
    name: str                # 对象名（文件或文件夹名）
    res_type: str            # "folder" | "knowledge_file"
    lib_name: str            # 库名（路径第一段）
    lib_type: str            # knowledge | department | user
    space_name: str          # BISHENG 空间名
    space_root_gns: str      # 空间对应的库/部门根 GNS（写入 SyncSpaceMapping）
    subdir_components: list  # 空间根之下的目录段（需在空间内逐层创建）


_ACTION_MAP: dict[tuple[int, int], EventAction] = {
    # Document operations
    (12, 2):  EventAction.SYNC_FILE,    # 上传/创建文件
    (12, 4):  EventAction.SYNC_FILE,    # 秒传修改文件
    (12, 11): EventAction.SYNC_ACL,     # 权限变更
    (12, 19): EventAction.SYNC_FILE,    # 重命名（重新同步）
    (12, 22): EventAction.SYNC_FOLDER,  # 新建文件夹
    (12, 24): EventAction.IGNORE,       # 发起自定义申请（非复制文件，无对象路径可解析）
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

# 事件处理依赖顺序：先建文件夹 → 再传文件 → 再授权 → 最后删除/组织。
# 日志按时间倒序拉取时，同名文件的「实名共享」常排在「上传」之前，
# 若按原始顺序处理，ACL 会因目标文件尚未同步而失败。
_ACTION_ORDER: dict[EventAction, int] = {
    EventAction.SYNC_FOLDER: 0,
    EventAction.SYNC_FILE: 1,
    EventAction.SYNC_ACL: 2,
    EventAction.DELETE: 3,
    EventAction.CREATE_DEPT: 4,
    EventAction.CREATE_USER: 5,
    EventAction.UPDATE_USER_DEPT: 6,
    EventAction.IGNORE: 99,
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
        from app.config import cfg
        configured = cfg.sync.get("console_delete_op_types", [])
        self._delete_op_types = {int(value) for value in configured}
        # 路径/名字驱动解析器（懒加载）
        self._resolver: PathResolver | None = None
        # BISHENG 子项缓存：{(space_id, parent_id) -> {name: folder_id}}
        self._bs_children_cache: dict[tuple[int, int | None], dict[str, int]] = {}
        # 空间缓存：space_root_gns -> space_id
        self._space_cache: dict[str, int] = {}

    def _action_for(self, event: LogEvent) -> EventAction:
        if event.log_type == 12 and event.op_type in self._delete_op_types:
            return EventAction.DELETE
        return event.action

    def handle(self, events: list[dict]) -> dict:
        self._stats = {a: 0 for a in EventAction}
        errors = 0
        # 先解析 + 按依赖顺序排序（stable sort，同类内部保持原始顺序），
        # 保证文件夹/文件在 ACL 之前落地，避免「实名共享早于上传」导致授权失败。
        parsed = []
        for entry in events:
            event = self._parse(entry)
            parsed.append((self._action_for(event), event))
        parsed.sort(key=lambda pair: _ACTION_ORDER.get(pair[0], 99))
        for action, event in parsed:
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
        """OP11: 实名共享/ACL 变更 — 路径/名字驱动定位对象并重新授权。"""
        t = self._resolve_target(event, res_type_hint=self._acl_res_type(event))
        if not t:
            raise RuntimeError(f"Cannot resolve target for ACL event {event.obj_id}")
        space_id = self._ensure_space(t)
        self._pipeline._mapper.set_api_context(self._pipeline._bs_perm, space_id)
        bs_id = (self._pipeline._folder_map.get(t.gns)
                 if t.res_type == "folder" else self._pipeline._file_map.get(t.gns))
        if not bs_id:
            # 兜底：映射未命中（对象从未全量同步过），按「父目录 + 名字」到 BISHENG 现查。
            # 命中则落映射后继续授权，彻底摆脱对全量映射的依赖。
            parent_id = self._ensure_parent_chain(t, space_id)
            bs_id = self._find_bs_resource(space_id, parent_id, t.name, t.res_type)
            if not bs_id:
                logger.warning("ACL fallback: %s %r not in BISHENG; skip "
                               "(re-applied when the resource gets synced)",
                               t.res_type, t.name[:40])
                return
            self._remember_acl_resource(event, t, bs_id, space_id)
            logger.info("ACL fallback: found %s %r -> BS id=%s",
                        t.res_type, t.name[:40], bs_id)
        grants = self._pipeline._build_grants_for_gns(t.gns)
        if grants and not self._pipeline._bs_perm.authorize(
                t.res_type, bs_id, grants=grants, timeout=60, retries=2):
            raise RuntimeError(f"BISHENG authorization failed for {t.res_type} {bs_id}")

    def _handle_new_folder(self, event: LogEvent):
        """OP22: 新建文件夹 — 路径/名字驱动定位父目录，自动建空间+父链+落库。"""
        t = self._resolve_target(event, res_type_hint="folder")
        if not t:
            raise RuntimeError(f"Cannot resolve target for folder event {event.obj_id}")
        space_id = self._ensure_space(t)
        self._pipeline._mapper.set_api_context(self._pipeline._bs_perm, space_id)
        parent_id = self._ensure_parent_chain(t, space_id)

        existing_id = self._pipeline._folder_map.get(t.gns)
        if existing_id:
            grants = self._pipeline._build_grants_for_gns(t.gns)
            if grants and not self._pipeline._bs_perm.authorize(
                    "folder", existing_id, grants=grants, timeout=60, retries=2):
                raise RuntimeError(
                    f"BISHENG authorization failed for folder {existing_id}")
            logger.info("Reused existing folder for replay: %s -> BS id=%s",
                        t.name[:40], existing_id)
            return

        fid = self._pipeline._bs_folder.create(space_id, t.name, parent_id=parent_id)
        self._pipeline._folder_map[t.gns] = fid
        self._pipeline._gns_to_name[t.gns] = t.name
        self._pipeline._uuid_to_gns[event.obj_id] = t.gns
        self._pipeline.persist_event_mapping(
            t.gns, fid, "folder", name=t.name, space_id=space_id)

        grants = self._pipeline._build_grants_for_gns(t.gns)
        if grants and not self._pipeline._bs_perm.authorize(
                "folder", fid, grants=grants, timeout=60, retries=2):
            raise RuntimeError(f"BISHENG authorization failed for folder {fid}")
        logger.info(f"Synced new folder: {t.name[:40]} -> BS id={fid}")

    def _handle_new_file(self, event: LogEvent):
        """OP2/19/24: 上传/修改/复制文件 — 路径/名字驱动定位，下载→上传→注册→落库。"""
        t = self._resolve_target(event, res_type_hint="knowledge_file")
        if not t:
            raise RuntimeError(f"Cannot resolve target for file event {event.obj_id}")
        if t.name.lower().endswith(tuple(SKIP_EXTENSIONS)):
            logger.info("Skip archive in incremental: %s", t.name[:50])
            return
        space_id = self._ensure_space(t)
        self._pipeline._mapper.set_api_context(self._pipeline._bs_perm, space_id)
        parent_id = self._ensure_parent_chain(t, space_id)

        existing_id = self._pipeline._file_map.get(t.gns)
        if existing_id and event.op_type in (2, 24):
            grants = self._pipeline._build_grants_for_gns(t.gns)
            if grants and not self._pipeline._bs_perm.authorize(
                    "knowledge_file", existing_id, grants=grants,
                    timeout=60, retries=2):
                raise RuntimeError(
                    f"BISHENG authorization failed for file {existing_id}")
            logger.info("Reused existing file for replay: %s -> BS id=%s",
                        t.name[:50], existing_id)
            return
        replacing_id = existing_id if existing_id and event.op_type in (4, 19) else None

        tmp = None
        try:
            tmp, local = self._download_anyshare(t.gns, t.name)
            fp = self._pipeline._bs_file.upload_to_minio(space_id, local)
            reg = self._pipeline._bs_file.register(space_id, fp, parent_id=parent_id)
            fid = reg["id"]
            self._pipeline._file_map[t.gns] = fid
            self._pipeline._gns_to_name[t.gns] = t.name
            self._pipeline._uuid_to_gns[event.obj_id] = t.gns
            self._pipeline.persist_event_mapping(
                t.gns, fid, "knowledge_file", name=t.name, space_id=space_id)

            if replacing_id and replacing_id != fid:
                self._pipeline._bs_file.delete_file(space_id, replacing_id)

            grants = self._pipeline._build_grants_for_gns(t.gns)
            if grants and not self._pipeline._bs_perm.authorize(
                    "knowledge_file", fid, grants=grants, timeout=60, retries=2):
                raise RuntimeError(f"BISHENG authorization failed for file {fid}")
            logger.info(f"Synced new file: {t.name[:50]} -> BS id={fid} parent={parent_id}")
        except Exception as e:
            logger.error(f"Failed to sync file {t.gns} ({t.name[:40]}): {e}")
            raise
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════
    # 路径/名字驱动解析与自动建空间/父链
    # ═══════════════════════════════════════════════════════════

    def _get_resolver(self) -> PathResolver:
        if self._resolver is None:
            self._resolver = PathResolver(
                self._pipeline._as_base, self._pipeline._get_as_token)
            self._resolver.load_libraries()
        return self._resolver

    def _resolve_target(self, event: LogEvent,
                        res_type_hint: str = "") -> ResolvedTarget | None:
        """路径/名字驱动解析目标对象及其 BISHENG 落地上下文。

        - OP2/22: exMsg 父路径 + msg 名字 → 父 GNS → 目标 GNS = 父GNS + objId
        - OP11:   exMsg 对象路径 → 父 GNS → 目标 GNS = 父GNS + objId
        """
        op = event.op_type
        ex = event.ex_msg
        name = self._extract_quoted_name(event.msg)

        if op == 11:
            obj_path = extract_object_path(ex)
            if not obj_path:
                return None
            comps = _split_path(obj_path)
            if not comps:
                return None
            parent_comps = comps[:-1]
            if not name:
                name = comps[-1]
            res_type = res_type_hint or "knowledge_file"
        else:
            parent_path = extract_parent_path(ex)
            if not parent_path:
                return None
            parent_comps = _split_path(parent_path)
            if not name:
                return None
            res_type = "folder" if op == 22 else "knowledge_file"

        if not parent_comps:
            return None
        resolver = self._get_resolver()
        lib_name = parent_comps[0]
        lib_type = resolver.library_type(lib_name) or "user"
        lib_gns = resolver.library_gns(lib_name)
        if not lib_gns:
            return None

        # 空间边界：部门库在「部门」层（第4段）切空间，其余在「库」层（第1段）。
        if lib_type == "department" and len(parent_comps) >= _DEPT_SPACE_DEPTH:
            space_comps = parent_comps[:_DEPT_SPACE_DEPTH]
            space_name = parent_comps[_DEPT_SPACE_DEPTH - 1]
            subdir_components = parent_comps[_DEPT_SPACE_DEPTH:]
        else:
            space_comps = parent_comps[:1]
            space_name = lib_name
            subdir_components = parent_comps[1:]

        parent_gns = resolver.resolve_dir(parent_comps)
        if not parent_gns:
            return None
        space_root_gns = resolver.resolve_dir(space_comps) or lib_gns

        return ResolvedTarget(
            gns=f"{parent_gns}/{event.obj_id}",
            parent_gns=parent_gns,
            name=name,
            res_type=res_type,
            lib_name=lib_name,
            lib_type=lib_type,
            space_name=space_name,
            space_root_gns=space_root_gns,
            subdir_components=list(subdir_components),
        )

    def _ensure_space(self, t: ResolvedTarget) -> int:
        """确保库/部门对应 BISHENG 空间存在，没有就自动新建并落 SyncSpaceMapping。

        复用优先级：DB 映射(按 GNS) → BISHENG 同名 → BISHENG 基名唯一匹配。
        基名回退解决全量(短名「人力资源部」)与增量(实际名「人力资源部（党委组织部_干部部）」)
        命名不一致导致的重复建空间问题。
        """
        if t.space_root_gns in self._space_cache:
            return self._space_cache[t.space_root_gns]
        space_id = self._lookup_space_mapping(t.space_root_gns)
        if space_id:
            self._space_cache[t.space_root_gns] = space_id
            return space_id
        mine = self._pipeline._bs_space.list_mine()
        for sp in mine:
            if sp.get("name") == t.space_name:
                self._space_cache[t.space_root_gns] = sp["id"]
                self._write_space_mapping(t, sp["id"])
                return sp["id"]
        base = self._base_name(t.space_name)
        base_matches = [sp for sp in mine if self._base_name(sp.get("name", "")) == base]
        if len(base_matches) == 1:
            self._space_cache[t.space_root_gns] = base_matches[0]["id"]
            self._write_space_mapping(t, base_matches[0]["id"])
            return base_matches[0]["id"]
        description = (f"AnyShare部门文档库 - {t.space_name}"
                       if t.lib_type == "department"
                       else f"AnyShare文档库 - {t.space_name}")
        space_id = self._pipeline._bs_space.create_personal(t.space_name, description)
        self._space_cache[t.space_root_gns] = space_id
        self._write_space_mapping(t, space_id)
        return space_id

    @staticmethod
    def _base_name(name: str) -> str:
        """去掉括号后缀：'人力资源部（党委组织部_干部部）' -> '人力资源部'。"""
        for sep in ("（", "(", "【", "["):
            idx = name.find(sep)
            if idx > 0:
                return name[:idx].strip()
        return name.strip()

    def _ensure_parent_chain(self, t: ResolvedTarget, space_id: int) -> int | None:
        """确保空间内父目录链存在，返回最深父目录 BISHENG folder_id（None=空间根）。"""
        parent_id: int | None = None
        current_gns = t.space_root_gns
        for name in t.subdir_components:
            child = self._get_resolver().child_gns(current_gns, name)
            if not child:
                raise RuntimeError(
                    f"Cannot resolve child dir {name!r} under {current_gns[-12:]}")
            current_gns = child
            parent_id = self._find_or_create_folder(space_id, current_gns, name, parent_id)
        return parent_id

    def _find_or_create_folder(self, space_id: int, gns: str, name: str,
                               parent_id: int | None) -> int:
        """按 (父目录, 名称) 复用或新建 BISHENG 文件夹，落 SyncFolderMapping。"""
        fid = self._pipeline._folder_map.get(gns)
        if fid:
            return fid
        cache_key = (space_id, parent_id)
        if cache_key not in self._bs_children_cache:
            self._bs_children_cache[cache_key] = self._load_bs_children(space_id, parent_id)
        fid = self._bs_children_cache[cache_key].get(name)
        if fid:
            self._remember_folder(gns, name, fid)
            return fid
        fid = self._pipeline._bs_folder.create(space_id, name, parent_id=parent_id)
        self._remember_folder(gns, name, fid)
        self._pipeline.persist_event_mapping(gns, fid, "folder", name=name, space_id=space_id)
        self._bs_children_cache[cache_key][name] = fid
        return fid

    def _remember_folder(self, gns: str, name: str, fid: int):
        self._pipeline._folder_map[gns] = fid
        self._pipeline._gns_to_name[gns] = name
        self._pipeline._uuid_to_gns[self._pipeline._extract_uuid(gns)] = gns

    def _load_bs_children(self, space_id: int, parent_id: int | None) -> dict[str, int]:
        """返回某父目录下已存在的 {文件夹名: folder_id}。page_size=500 一次拿全。"""
        result: dict[str, int] = {}
        params = {"page": 1, "page_size": 500}
        if parent_id:
            params["parent_id"] = parent_id
        r = self._pipeline._bs._get(
            f"/api/v1/knowledge/space/{space_id}/children", params=params)
        data = self._pipeline._bs.ok(r).get("data", {})
        for item in data.get("data", []):
            if item.get("file_type") == 0 and item.get("file_name"):
                result[item["file_name"]] = item["id"]
        return result

    def _find_bs_resource(self, space_id: int, parent_id: int | None,
                          name: str, res_type: str) -> int | None:
        """按名字在 BISHENG 父目录下现查资源 ID（folder 或 knowledge_file）。

        用于 ACL 兜底：对象从未全量同步、映射表未命中时，按「父目录 + 名称」定位。
        """
        want_folder = res_type == "folder"
        params = {"page": 1, "page_size": 500}
        if parent_id:
            params["parent_id"] = parent_id
        r = self._pipeline._bs._get(
            f"/api/v1/knowledge/space/{space_id}/children", params=params)
        data = self._pipeline._bs.ok(r).get("data", {})
        for item in data.get("data", []):
            if item.get("file_name") != name:
                continue
            if (item.get("file_type") == 0) == want_folder:
                return item["id"]
        return None

    def _remember_acl_resource(self, event: LogEvent, t: ResolvedTarget,
                               bs_id: int, space_id: int):
        """ACL 兜底命中后，把「GNS → BISHENG id」记入内存并落库，供后续增量复用。"""
        if t.res_type == "folder":
            self._pipeline._folder_map[t.gns] = bs_id
        else:
            self._pipeline._file_map[t.gns] = bs_id
        self._pipeline._gns_to_name[t.gns] = t.name
        self._pipeline._uuid_to_gns[event.obj_id] = t.gns
        self._pipeline.persist_event_mapping(
            t.gns, bs_id, t.res_type, name=t.name, space_id=space_id)

    def _download_anyshare(self, docid: str, name: str) -> tuple[Path, Path]:
        """从 AnyShare 下载文件到临时目录，返回 (tmp_dir, local_path)。"""
        for attempt in range(3):
            try:
                r = httpx.post(
                    f"{self._pipeline._as_base}/api/efast/v1/file/osdownload",
                    json={"docid": docid, "rev": "", "authtype": "QUERY_STRING",
                          "savename": name, "usehttps": True},
                    headers={"Authorization": f"Bearer {self._pipeline._get_as_token()}"},
                    timeout=30)
                r.raise_for_status()
                break
            except httpx.TransportError:
                if attempt == 2:
                    raise
                import time as time_mod
                time_mod.sleep(2 * (attempt + 1))
        auth_req = r.json().get("authrequest")
        if not auth_req:
            raise RuntimeError(f"No authrequest for {docid}: {r.text[:100]}")
        headers = {}
        for h in auth_req[2:]:
            if ": " in h:
                k, v = h.split(": ", 1)
                headers[k] = v
        safe_name = "".join(c for c in name if c.isalnum() or c in "._-()（）")
        tmp = Path.home() / "AppData" / "Local" / "Temp" / "as_sync" / uuid_mod.uuid4().hex[:8]
        tmp.mkdir(parents=True, exist_ok=True)
        local = tmp / safe_name
        for attempt in range(3):
            try:
                with httpx.Client(timeout=120) as cc:
                    with cc.stream(auth_req[0], auth_req[1], headers=headers) as rr:
                        rr.raise_for_status()
                        with open(local, "wb") as fh:
                            for chunk in rr.iter_bytes(65536):
                                fh.write(chunk)
                break
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadTimeout):
                local.unlink(missing_ok=True)
                if attempt == 2:
                    raise
                import time as time_mod
                time_mod.sleep(3 * (attempt + 1))
        return tmp, local

    @staticmethod
    def _acl_res_type(event: LogEvent) -> str:
        """从 msg 判断 ACL 对象是文件夹还是文件。"""
        return "folder" if "文件夹" in event.msg else "knowledge_file"

    @staticmethod
    def _extract_quoted_name(msg: str) -> str:
        """从 msg 提取第一个引号内对象名（上传文件"X" / 新建文件夹"X" / 将文件"X"给）。"""
        m = re.search(
            r'[“”「」《》"\']([^“”「」《》"\']+)'
            r'[“”「」《》"\']',
            msg or "")
        return m.group(1).strip() if m else ""

    @staticmethod
    def _lookup_space_mapping(source_doc_lib_id: str) -> int | None:
        try:
            from app.models import get_session
            from app.models.space_mapping import SyncSpaceMapping
            from sqlmodel import select
            with get_session() as s:
                sm = s.exec(select(SyncSpaceMapping).where(
                    SyncSpaceMapping.source_doc_lib_id == source_doc_lib_id)).first()
                if sm and sm.target_space_id:
                    return sm.target_space_id
        except Exception as e:
            logger.warning(f"lookup_space_mapping error: {e}")
        return None

    @staticmethod
    def _write_space_mapping(t: ResolvedTarget, space_id: int):
        try:
            from app.models import get_session, init_db
            from app.models.space_mapping import SyncSpaceMapping
            from sqlmodel import select
            init_db()
            with get_session() as s:
                sm = s.exec(select(SyncSpaceMapping).where(
                    SyncSpaceMapping.source_doc_lib_id == t.space_root_gns)).first()
                if sm:
                    sm.target_space_id = space_id
                    sm.status = "created"
                else:
                    source_type = ("dept_doc_lib" if t.lib_type == "department"
                                   else "knowledge_doc_lib" if t.lib_type == "knowledge"
                                   else "user_doc_lib")
                    s.add(SyncSpaceMapping(
                        tenant_id=1, source_doc_lib_id=t.space_root_gns,
                        source_doc_lib_name=t.space_name, source_type=source_type,
                        target_space_id=space_id, status="created"))
                s.commit()
        except Exception as e:
            logger.warning(f"write_space_mapping error: {e}")

    def _handle_delete(self, event: LogEvent):
        """OP3/OP24: Delete file/folder from BISHENG and mark DB as deleted."""
        gns = self._pipeline.resolve_uuid(event.obj_id)
        if not gns:
            raise RuntimeError(f"Cannot resolve GNS for delete event {event.obj_id}")

        is_folder = gns in self._pipeline._folder_map
        bs_id = (self._pipeline._folder_map.get(gns)
                 if is_folder else self._pipeline._file_map.get(gns))

        if bs_id:
            try:
                space_id = self._resolve_space_id_from_gns(gns)
                if not space_id:
                    raise RuntimeError(f"Cannot resolve space_id for delete {gns}")
                if is_folder:
                    self._pipeline._bs_folder.delete(space_id, bs_id)
                    del self._pipeline._folder_map[gns]
                else:
                    self._pipeline._bs_file.delete_file(space_id, bs_id)
                    del self._pipeline._file_map[gns]
                logger.info(f"Deleted {'folder' if is_folder else 'file'} bs_id={bs_id} gns={gns[-20:]}")
            except Exception as e:
                logger.warning(f"BISHENG delete failed bs_id={bs_id}: {e}")
                raise

        from app.models import get_session
        from app.models.document_mapping import SyncDocumentMapping
        from app.models.folder_mapping import SyncFolderMapping
        from sqlmodel import select
        with get_session() as s:
            dm = s.exec(select(SyncDocumentMapping).where(
                SyncDocumentMapping.source_doc_id == gns)).first()
            if dm:
                dm.status = "deleted"
                s.commit()
                logger.info(f"Marked deleted in DB: {dm.source_name}")
            fm = s.exec(select(SyncFolderMapping).where(
                SyncFolderMapping.source_folder_id == gns)).first()
            if fm:
                fm.status = "deleted"
                s.commit()
                logger.info(f"Marked folder deleted in DB: {fm.source_name}")

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
            r = self._pipeline._bs._get(
                "/api/v1/user/list",
                params={"keyword": username, "page": 1, "page_size": 5},
                timeout=15)
            data = self._pipeline._bs.ok(r)
            for u in data.get("data", {}).get("data", []):
                if u.get("external_id") == username or u.get("user_name") == display_name:
                    logger.debug(f"User already exists: {username}")
                    return

            # Create user via regist API (same as import_org.py)
            r2 = self._pipeline._bs._post(
                "/api/v1/user/regist",
                {"user_name": display_name or username,
                 "user_id": username,
                 "password": "Sync@123456",
                 "source": "local"},
                timeout=15)
            self._pipeline._bs.ok(r2)
            logger.info(f"Created user: {username}")
        except Exception as e:
            logger.warning(f"Create user error {username}: {e}")
            raise

    def _handle_create_dept(self, event: LogEvent):
        """OP1 in LT11: Create department in BISHENG.

        Parses msg like: '新建 部门安全环保办公成成功'
        """
        dept_name = self._parse_dept_from_msg(event.msg)
        if not dept_name:
            return

        try:
            # Check if department exists via departments/children API
            r = self._pipeline._bs._get(
                "/api/v1/departments/children",
                params={"parent_id": 1, "include_archived": "false"}, timeout=15)
            children = self._pipeline._bs.ok(r).get("data", {}).get("children", [])
            if any(d.get("name") == dept_name for d in children):
                logger.debug(f"Dept already exists: {dept_name}")
                return

            # Create department
            r2 = self._pipeline._bs._post(
                "/api/v1/department", {"name": dept_name, "parent_id": 1},
                timeout=15)
            self._pipeline._bs.ok(r2)
            logger.info(f"Created department: {dept_name}")
        except Exception as e:
            logger.warning(f"Create dept error {dept_name}: {e}")
            raise

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
            r = self._pipeline._bs._get(
                "/api/v1/user/list",
                params={"keyword": display_name or username, "page": 1, "page_size": 10},
                timeout=15)
            users = self._pipeline._bs.ok(r).get("data", {}).get("data", [])
            bs_user = next(
                (u for u in users if u.get("external_id") == username
                 or u.get("user_name") == display_name),
                None)
            if not bs_user:
                # 再用 username 搜一次
                r2 = self._pipeline._bs._get(
                    "/api/v1/user/list",
                    params={"keyword": username, "page": 1, "page_size": 5},
                    timeout=15)
                users2 = self._pipeline._bs.ok(r2).get("data", {}).get("data", [])
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
            r2 = self._pipeline._bs._get(
                "/api/v1/departments/children",
                params={"parent_id": 1, "include_archived": "false"}, timeout=15)
            dept_data = self._pipeline._bs.ok(r2)
            dept_id = None
            for d in dept_data.get("data", {}).get("children", []):
                if d.get("name") == target_dept:
                    dept_id = d.get("id")
                    break
                found = _find_dept_node(d.get("children", []), target_dept)
                if found:
                    dept_id = found
                    break

            if not dept_id:
                logger.warning(f"User dept change: dept not found in BISHENG: {target_dept}")
                return

            # Update user's department
            r3 = self._pipeline._bs._put(
                f"/api/v1/user/{user_id}", {"department_id": dept_id}, timeout=15)
            self._pipeline._bs.ok(r3)
            logger.info(f"User {username} moved to dept {target_dept} (id={dept_id})")

        except Exception as e:
            logger.warning(f"User dept change error {username}: {e}")
            raise

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
