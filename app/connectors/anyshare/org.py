"""AnyShare organization APIs — users, departments, groups."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AnyShareOrg:
    """Fetches users, departments, and user groups from AnyShare."""

    def __init__(self, base_url: str, get_app_token, timeout: float = 30.0):
        self._url = base_url.rstrip("/")
        self._get_token = get_app_token
        self._timeout = timeout

    def _post(self, path: str, body: dict) -> dict:
        with httpx.Client(timeout=httpx.Timeout(self._timeout)) as client:
            resp = client.post(
                f"{self._url}/api{path}",
                json=body,
                headers={"Authorization": f"Bearer {self._get_token()}"},
            )
            resp.raise_for_status()
            return resp.json()

    def list_users(self, start: int = 0, limit: int = 100) -> list[dict]:
        """Paginated user list."""
        data = self._post("/eacp/v1/organization/getalluser", {"start": start, "limit": limit})
        return data.get("users", [])

    def count_users(self) -> int:
        data = self._post("/eacp/v1/organization/getallusercount", {})
        return data.get("total", 0)

    def get_user_by_id(self, user_id: str) -> dict | None:
        data = self._post("/eacp/v1/organization/getuserbyid", {"user_id": user_id})
        return data.get("user")

    def list_departments(self, parent_id: str = "root") -> list[dict]:
        data = self._post("/eacp/v1/organization/getsubdepsbydepid", {"dep_id": parent_id})
        return data.get("deps", [])

    def get_department_by_id(self, dep_id: str) -> dict | None:
        data = self._post("/eacp/v1/organization/getdepbyid", {"dep_id": dep_id})
        return data.get("dep")

    def list_department_users(self, dep_id: str) -> list[dict]:
        data = self._post("/eacp/v1/organization/getsubusersbydepid", {"dep_id": dep_id})
        return data.get("users", [])

    def list_groups(self) -> list[dict]:
        """User groups (contactors)."""
        with httpx.Client(timeout=httpx.Timeout(self._timeout)) as client:
            resp = client.get(
                f"{self._url}/api/eacp/v1/contactor/getgroups",
                headers={"Authorization": f"Bearer {self._get_token()}"},
            )
            resp.raise_for_status()
            return resp.json().get("groups", [])

    def list_group_members(self, group_id: str) -> list[dict]:
        with httpx.Client(timeout=httpx.Timeout(self._timeout)) as client:
            resp = client.get(
                f"{self._url}/api/eacp/v1/contactor/get",
                params={"group_id": group_id},
                headers={"Authorization": f"Bearer {self._get_token()}"},
            )
            resp.raise_for_status()
            return resp.json().get("persons", [])
