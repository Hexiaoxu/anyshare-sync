"""Identity mapping: AnyShare users/departments/groups → BISHENG IDs.

Mapping priority:
  1. Parse accessorname: "5jqianw/**eisoo**/钱卫" → username="5jqianw", display_name="钱卫"
  2. Match display_name → BISHENG user_name (Chinese name)
  3. Match username → BISHENG external_id
  4. Live API lookup via BishengPermission if pre-loaded data insufficient
  5. Manual resolution (identity_pending)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

# Separator in AnyShare accessorname: username/**eisoo**/display_name
_EISOO_SEP = "/**eisoo**/"


class PrincipalType(str, Enum):
    USER = "user"
    DEPARTMENT = "department"
    GROUP = "group"


class MappingStatus(str, Enum):
    MAPPED = "mapped"
    IDENTITY_PENDING = "identity_pending"
    CONFLICT = "conflict"
    DISABLED = "disabled"


@dataclass
class PrincipalMapping:
    source_id: str
    source_type: PrincipalType
    source_name: str               # original accessorname or dept name
    target_id: int | None = None
    status: MappingStatus = MappingStatus.IDENTITY_PENDING
    match_method: str = ""         # "external_id" | "user_name" | "display_name" | "manual" | "api"
    conflict_candidates: list[int] | None = None


def parse_accessorname(accessorname: str) -> tuple[str, str]:
    """Parse AnyShare accessorname into (username, display_name).

    Format A (user): "5jqianw/**eisoo**/钱卫" → ("5jqianw", "钱卫")
    Format B (dept):  "中国水利水电第五工程局有限公司" → ("", "中国水利水电第五工程局有限公司")
    """
    if _EISOO_SEP in accessorname:
        parts = accessorname.split(_EISOO_SEP, 1)
        return parts[0], parts[1] if len(parts) > 1 else ""
    return "", accessorname


class PrincipalMapper:
    """Maps AnyShare principals to BISHENG target IDs.

    Supports both pre-loaded batch mapping and on-demand API lookup.
    """

    def __init__(self, bs_permission=None):
        """Initialize mapper.

        Args:
            bs_permission: Optional BishengPermission client for live API lookups.
        """
        self._mappings: dict[str, PrincipalMapping] = {}  # keyed by source_id
        self._bs_perm = bs_permission
        self._space_id: int | None = None  # set for API-based lookups

    def set_api_context(self, bs_permission, space_id: int):
        """Set API context for on-demand identity resolution."""
        self._bs_perm = bs_permission
        self._space_id = space_id

    # ── Batch mapping (pre-loaded data) ────────────────────

    def map_users(self, any_share_users: list[dict],
                  bisheng_users: list[dict]) -> list[PrincipalMapping]:
        """Map AnyShare users to BISHENG user_ids.

        Input any_share_users should have at minimum: {"id": ..., "name": ...}
        where 'name' is the raw accessorname (e.g. "5jqianw/**eisoo**/钱卫").
        """
        # Build lookup: external_id → bisheng user, user_name → [bisheng users]
        by_ext_id: dict[str, dict] = {}
        by_name: dict[str, list[dict]] = {}
        for u in bisheng_users:
            ext = (u.get("external_id") or "").strip()
            if ext:
                by_ext_id[ext] = u
            name = (u.get("user_name") or "").strip()
            if name:
                by_name.setdefault(name, []).append(u)

        results = []
        for au in any_share_users:
            au_id = str(au.get("id", ""))
            raw_name = au.get("name", "")
            username, display_name = parse_accessorname(raw_name)
            mapping = PrincipalMapping(
                source_id=au_id,
                source_type=PrincipalType.USER,
                source_name=raw_name,
            )

            # Priority 1: match display_name → BISHENG user_name
            if display_name and display_name in by_name and len(by_name[display_name]) == 1:
                mapping.target_id = by_name[display_name][0]["user_id"]
                mapping.status = MappingStatus.MAPPED
                mapping.match_method = "display_name"
            # Priority 2: match username → BISHENG external_id
            elif username and username in by_ext_id:
                mapping.target_id = by_ext_id[username]["user_id"]
                mapping.status = MappingStatus.MAPPED
                mapping.match_method = "external_id"
            # Priority 3: match raw name → BISHENG external_id (fallback for non-eisoo format)
            elif raw_name in by_ext_id:
                mapping.target_id = by_ext_id[raw_name]["user_id"]
                mapping.status = MappingStatus.MAPPED
                mapping.match_method = "external_id"
            # Priority 4: unique name match on raw_name
            elif raw_name in by_name and len(by_name[raw_name]) == 1:
                mapping.target_id = by_name[raw_name][0]["user_id"]
                mapping.status = MappingStatus.MAPPED
                mapping.match_method = "user_name"
            # Priority 5: multiple matches = conflict
            elif raw_name in by_name and len(by_name[raw_name]) > 1:
                mapping.status = MappingStatus.CONFLICT
                mapping.conflict_candidates = [u["user_id"] for u in by_name[raw_name]]
            # Priority 6: no match = pending (will try API lookup)
            else:
                mapping.status = MappingStatus.IDENTITY_PENDING

            self._mappings[au_id] = mapping
            results.append(mapping)

        mapped = sum(1 for r in results if r.status == MappingStatus.MAPPED)
        logger.info(f"User mapping: {mapped} mapped, "
                     f"{sum(1 for r in results if r.status == MappingStatus.IDENTITY_PENDING)} pending, "
                     f"{sum(1 for r in results if r.status == MappingStatus.CONFLICT)} conflicts")
        return results

    def map_departments(self, any_share_depts: list[dict],
                        bisheng_depts: list[dict]) -> list[PrincipalMapping]:
        """Map AnyShare departments to BISHENG department_ids."""
        by_ext_id = {str(d.get("external_id", "") or ""): d for d in bisheng_depts if d.get("external_id")}
        by_name: dict[str, list] = {}
        for d in bisheng_depts:
            name = (d.get("name") or "").strip()
            if name:
                by_name.setdefault(name, []).append(d)

        results = []
        for ad in any_share_depts:
            ad_id = str(ad.get("id", ""))
            ad_name = ad.get("name", "")
            mapping = PrincipalMapping(
                source_id=ad_id,
                source_type=PrincipalType.DEPARTMENT,
                source_name=ad_name,
            )
            if ad_id in by_ext_id:
                mapping.target_id = by_ext_id[ad_id]["id"]
                mapping.status = MappingStatus.MAPPED
                mapping.match_method = "external_id"
            elif ad_name in by_name and len(by_name[ad_name]) == 1:
                mapping.target_id = by_name[ad_name][0]["id"]
                mapping.status = MappingStatus.MAPPED
                mapping.match_method = "name"
            else:
                mapping.status = MappingStatus.IDENTITY_PENDING

            self._mappings[ad_id] = mapping
            results.append(mapping)

        return results

    # ── On-demand API lookup ───────────────────────────────

    def resolve_principal(self, accessorname: str,
                          principal_type: str = "user") -> int | None:
        """Resolve a single AnyShare accessor to BISHENG ID via map or API.

        Results are cached — subsequent calls with same accessorname return instantly.
        """
        # 1. Cache hit — check by accessorname (used as mapping key)
        if accessorname in self._mappings:
            m = self._mappings[accessorname]
            if m.status == MappingStatus.MAPPED and m.target_id is not None:
                return m.target_id

        # 2. Cache hit — check by source_name
        for m in self._mappings.values():
            if m.status == MappingStatus.MAPPED and m.source_name == accessorname:
                return m.target_id

        # 3. Live API lookup
        username, display_name = parse_accessorname(accessorname)

        if principal_type == "user":
            if self._bs_perm and self._space_id:
                try:
                    search_name = display_name or username
                    results = self._bs_perm.search_grant_users(
                        self._space_id, keyword=search_name, page_size=5)
                    for u in results:
                        uname = u.get("user_name", "")
                        ext_id = u.get("external_id", "")
                        # Match by display name or external_id
                        if uname == search_name or ext_id == username:
                            uid = u["user_id"]
                            # Cache the result
                            mapping = PrincipalMapping(
                                source_id=accessorname,
                                source_type=PrincipalType.USER,
                                source_name=accessorname,
                                target_id=uid,
                                status=MappingStatus.MAPPED,
                                match_method="api",
                            )
                            self._mappings[accessorname] = mapping
                            return uid
                except Exception as e:
                    logger.warning(f"API user lookup failed for {accessorname}: {e}")

        elif principal_type == "department":
            # Department live API lookup
            if self._bs_perm and self._space_id:
                try:
                    results = self._bs_perm.search_grant_departments(
                        self._space_id, keyword=accessorname, limit=5)
                    for d in results:
                        if d.get("name") == accessorname:
                            did = d["id"]
                            mapping = PrincipalMapping(
                                source_id=accessorname,
                                source_type=PrincipalType.DEPARTMENT,
                                source_name=accessorname,
                                target_id=did,
                                status=MappingStatus.MAPPED,
                                match_method="api",
                            )
                            self._mappings[accessorname] = mapping
                            return did
                except Exception as e:
                    logger.warning(f"API dept lookup failed for {accessorname}: {e}")

        return None

    # ── Accessors ───────────────────────────────────────────

    def get(self, source_id: str) -> PrincipalMapping | None:
        return self._mappings.get(source_id)

    def is_mapped(self, source_id: str) -> bool:
        m = self._mappings.get(source_id)
        return m is not None and m.status == MappingStatus.MAPPED

    def get_mapped_count(self) -> int:
        return sum(1 for m in self._mappings.values() if m.status == MappingStatus.MAPPED)
