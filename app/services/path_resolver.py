"""路径/名字驱动的 AnyShare 对象解析器（增量同步用）。

替代旧的 UUID→GNS 映射表方案。链路：

   日志 exMsg 人读路径
     → 库名 → 库根 GNS（list doc-lib 三类型）
     → 逐层 sub_objects 按名字反查子目录 GNS
     → 父目录 GNS
     → 目标完整 GNS = 父目录 GNS + "/" + objId

不依赖任何预存的 SyncFolderMapping / SyncDocumentMapping。
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# sub_objects 一次取全的上限（10000 会返回空，实测 5000 安全）
_CHILD_LIMIT = 5000

# 库类型 → doc-lib 列表接口
_DOCLIB_PATHS = (
    "/api/efast/v1/doc-lib/knowledge",
    "/api/efast/v1/doc-lib/department",
    "/api/efast/v1/doc-lib/user",
)


class PathResolver:
    """解析 AnyShare 人读路径 → 完整 GNS。

    Args:
        as_base: AnyShare base URL
        get_token: 返回 Bearer token 的 callable
    """

    def __init__(self, as_base: str, get_token, timeout: float = 30.0):
        self._base = as_base.rstrip("/")
        self._get_token = get_token
        self._timeout = timeout
        self._lib_gns: dict[str, str] = {}          # 库名 -> 库根 GNS
        self._lib_type: dict[str, str] = {}         # 库名 -> knowledge|department|user
        self._dir_children: dict[str, dict[str, str]] = {}  # 父GNS -> {子名: 子GNS}

    # ── 库发现 ───────────────────────────────────────────────

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}"}

    def load_libraries(self) -> int:
        """拉全三类型文档库，建 库名→GNS / 库名→类型 索引。返回库总数。"""
        self._lib_gns = {}
        self._lib_type = {}
        for path in _DOCLIB_PATHS:
            offset = 0
            while True:
                r = httpx.get(
                    f"{self._base}{path}",
                    params={"offset": offset, "limit": 100},
                    headers=self._headers(), timeout=self._timeout)
                if r.status_code != 200:
                    logger.warning("doc-lib %s HTTP %s", path, r.status_code)
                    break
                data = r.json()
                page = data.get("entries", data.get("doc_libs", []))
                for item in page:
                    name = item.get("name", "")
                    if name and item.get("id"):
                        self._lib_gns[name] = item["id"]
                        self._lib_type[name] = path.rsplit("/", 1)[-1]
                if len(page) < 100:
                    break
                offset += 100
        return len(self._lib_gns)

    def library_names(self) -> list[str]:
        return list(self._lib_gns.keys())

    def library_gns(self, name: str) -> str | None:
        return self._lib_gns.get(name)

    def library_type(self, name: str) -> str | None:
        """返回库类型：knowledge | department | user。"""
        return self._lib_type.get(name)

    def child_gns(self, parent_gns: str, child_name: str) -> str | None:
        """返回 parent_gns 下名为 child_name 的子目录 GNS（带缓存）。"""
        return self._sub_dirs(parent_gns).get(child_name)

    # ── 逐层反查 ─────────────────────────────────────────────

    def _sub_dirs(self, gns: str) -> dict[str, str]:
        """返回某目录下 {子目录名: 子目录GNS}，带缓存。"""
        if gns in self._dir_children:
            return self._dir_children[gns]
        r = httpx.get(
            f"{self._base}/api/efast/v1/folders/{quote(gns, safe='')}/sub_objects",
            params={"limit": _CHILD_LIMIT, "sort": "name", "direction": "asc"},
            headers=self._headers(), timeout=self._timeout)
        if r.status_code != 200:
            logger.warning("sub_objects %s HTTP %s", gns[:40], r.status_code)
            return {}
        sub = r.json()
        m = {d["name"]: d["id"] for d in sub.get("dirs", []) if d.get("name")}
        self._dir_children[gns] = m
        return m

    def resolve_dir(self, components: list[str]) -> str | None:
        """components = [库名, 目录1, 目录2, ...] → 最深目录的完整 GNS。

        任何一层名字匹配不到就返回 None。
        """
        if not components:
            return None
        gns = self._lib_gns.get(components[0])
        if not gns:
            logger.debug("库名未命中: %r", components[0])
            return None
        for name in components[1:]:
            children = self._sub_dirs(gns)
            nxt = children.get(name)
            if not nxt:
                logger.debug("目录未命中: %r (父 %s)", name, gns[-12:])
                return None
            gns = nxt
        return gns

    def resolve_parent_gns(self, parent_path: str) -> str | None:
        """父路径 → 父目录完整 GNS。

        parent_path 形如 "科研创新/专利查询/2026"（不含文件名）。
        """
        parts = _split_path(parent_path)
        return self.resolve_dir(parts)

    def resolve_object_gns(self, obj_path: str, obj_id: str) -> str | None:
        """对象路径（含文件名）→ 目标对象完整 GNS。

        obj_path 形如 "科研创新/专利查询/2026/xxx.DOC"（最后一段是文件名）。
        父目录 GNS = resolve_dir(去掉文件名后的部分)；
        目标 GNS = 父目录 GNS + "/" + objId。
        """
        parts = _split_path(obj_path)
        if not parts:
            return None
        parent_gns = self.resolve_dir(parts[:-1])
        if not parent_gns:
            return None
        return f"{parent_gns}/{obj_id}"


# ── exMsg 路径解析 ───────────────────────────────────────────

_PATH_RE = re.compile(
    r'(?:父路径|对象路径)[:：]\s*AnyShare://([^;，]+)')


def _split_path(raw: str) -> list[str]:
    """'科研创新/专利查询/2026' → ['科研创新', '专利查询', '2026']"""
    raw = raw.strip().strip("/")
    return [p.strip() for p in raw.split("/") if p.strip()]


def extract_parent_path(ex_msg: str) -> str | None:
    """从 exMsg 提取「父路径」人读路径（库名开头）。"""
    m = re.search(r'父路径[:：]\s*AnyShare://([^;，]+)', ex_msg or "")
    return m.group(1).strip() if m else None


def extract_object_path(ex_msg: str) -> str | None:
    """从 exMsg 提取「对象路径」人读路径（库名开头，含文件名）。"""
    m = re.search(r'对象路径[:：]\s*AnyShare://([^;，]+)', ex_msg or "")
    return m.group(1).strip() if m else None
