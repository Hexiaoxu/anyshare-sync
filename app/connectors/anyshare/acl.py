"""AnyShare ACL collection.

Collects raw ACL data from AnyShare for later translation to BISHENG FGA tuples.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class AclEntry:
    accessor_id: str
    accessor_type: str   # "user" | "department" | "contactor"
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    inherit: bool = False
    inherit_from: str | None = None
    expire_time: str | None = None


class AnyShareAcl:
    """Collects ACL and owner info from AnyShare."""

    def __init__(self, base_url: str, get_token, timeout: float = 30.0):
        self._url = base_url.rstrip("/")
        self._get_token = get_token
        self._timeout = timeout

    def get_acl(self, object_id: str) -> list[AclEntry]:
        """Get ACL for a file/folder."""
        try:
            with httpx.Client(timeout=httpx.Timeout(self._timeout)) as client:
                resp = client.post(
                    f"{self._url}/api/eacp/v1/perm2/get",
                    json={"docid": object_id},
                    headers={"Authorization": f"Bearer {self._get_token()}"},
                )
                resp.raise_for_status()
                raw = resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning(f"ACL fetch failed for {object_id}: {e}")
            return []

        entries = []
        perminfos = raw.get("perminfos", [])
        root_inherit = raw.get("inherit", False)
        for item in perminfos:
            endtime = item.get("endtime", -1)
            entries.append(AclEntry(
                accessor_id=item.get("accessorid", ""),
                accessor_type=item.get("accessortype", "user"),
                allow=item.get("allow", []),
                deny=item.get("deny", []),
                inherit=root_inherit,
                inherit_from=item.get("inheritdocid"),
                expire_time=None if endtime == -1 else str(endtime),
            ))
        return entries

    def check_permissions(self, object_id: str, account: str, get_user_token) -> list[str]:
        """Check what permissions the current user has on *object_id*.

        Used as a pre-download gate: must have 'download' to proceed.
        """
        token = get_user_token(account)
        try:
            with httpx.Client(timeout=httpx.Timeout(self._timeout)) as client:
                resp = client.post(
                    f"{self._url}/api/eacp/v1/perm1/checkall",
                    json={"docid": object_id},
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("permissions", data.get("allow", []))
        except httpx.HTTPStatusError as e:
            logger.warning(f"Permission check failed for {object_id}: {e}")
            return []

    def get_owner(self, object_id: str) -> dict | None:
        """Get owner info for an object."""
        try:
            with httpx.Client(timeout=httpx.Timeout(self._timeout)) as client:
                resp = client.post(
                    f"{self._url}/api/eacp/v1/owner/get",
                    json={"docid": object_id},
                    headers={"Authorization": f"Bearer {self._get_token()}"},
                )
                resp.raise_for_status()
                return resp.json().get("owner", resp.json().get("owners", [{}])[0] if resp.json().get("owners") else None)
        except httpx.HTTPStatusError:
            return None

    def serialize_acl(self, entries: list[AclEntry]) -> str:
        """Serialize ACL entries to JSON string for storage."""
        return json.dumps(
            [
                {
                    "accessor_id": e.accessor_id,
                    "accessor_type": e.accessor_type,
                    "allow": e.allow,
                    "deny": e.deny,
                    "inherit": e.inherit,
                    "inherit_from": e.inherit_from,
                    "expire_time": e.expire_time,
                }
                for e in entries
            ],
            ensure_ascii=False,
        )
