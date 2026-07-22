"""AnyShare Console/Admin API — department tree & user management.

These APIs require an admin console OAuth token (console.oauth2_token).
Paths confirmed against powerchina instance (v7.x).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class AnyShareDept:
    id: str
    name: str
    sub_dept_count: int = 0
    sub_user_count: int = 0
    parent_id: str = ""


@dataclass
class AnyShareUser:
    id: str
    name: str
    email: str = ""
    phone: str = ""
    department_ids: list[str] = None

    def __post_init__(self):
        if self.department_ids is None:
            self.department_ids = []


class AnyShareConsole:
    """Admin console APIs for org structure."""

    def __init__(self, base_url: str, get_token, timeout: float = 30.0):
        self._url = base_url.rstrip("/")
        self._get_token = get_token
        self._timeout = timeout

    def _post(self, path: str, body) -> list:
        with httpx.Client(timeout=httpx.Timeout(self._timeout)) as client:
            resp = client.post(
                f"{self._url}{path}",
                json=body,
                headers={"Authorization": f"Bearer {self._get_token()}"},
            )
            resp.raise_for_status()
            return resp.json()

    # ── Department Tree ─────────────────────────────────────

    def get_sub_departments(self, parent_dept_ids: list[str]) -> list[dict]:
        """Get child departments. Body is a JSON array of parent IDs."""
        return self._post(
            "/console/api/ShareMgnt/Usrm_GetSubDepartments",
            parent_dept_ids,
        )

    def get_dept_tree(self, root_id: str, max_depth: int = 10) -> list[AnyShareDept]:
        """Recursively fetch the full department tree."""
        all_depts: list[AnyShareDept] = []
        queue = [(root_id, "")]
        depth = 0

        while queue and depth < max_depth:
            level_ids = [q[0] for q in queue]
            parents = {q[0]: q[1] for q in queue}
            queue = []

            try:
                children = self.get_sub_departments(level_ids)
            except Exception as e:
                logger.error(f"Failed to get sub-depts at depth {depth}: {e}")
                break

            for child in children:
                dept = AnyShareDept(
                    id=child["id"],
                    name=child.get("name", ""),
                    sub_dept_count=child.get("subDepartmentCount", 0),
                    sub_user_count=child.get("subUserCount", 0),
                    parent_id=parents.get(child["id"], root_id),
                )
                all_depts.append(dept)

                if child.get("subDepartmentCount", 0) > 0:
                    queue.append((child["id"], child["id"]))

            depth += 1
            logger.info(f"Dept tree depth {depth}: {len(children)} depts")

        logger.info(f"Full dept tree: {len(all_depts)} departments")
        return all_depts

    # ── Users ────────────────────────────────────────────────

    def get_dept_users(self, dept_id: str) -> list[dict]:
        """Get users in a department."""
        return self._post(
            "/console/api/ShareMgnt/Usrm_GetSubUsers",
            [dept_id],
        )

    def get_all_users(self, root_dept_id: str) -> list[AnyShareUser]:
        """Get all users in an org tree (walks all departments)."""
        depts = self.get_dept_tree(root_dept_id)
        all_depts = [d for d in depts]
        # Also include root
        try:
            root_children = self.get_sub_departments([root_dept_id])
            all_depts = [AnyShareDept(id=root_dept_id, name="ROOT")] + [AnyShareDept(**c) for c in root_children] + depts
        except Exception:
            pass

        users: dict[str, AnyShareUser] = {}  # dedup by ID

        for dept in all_depts:
            if dept.sub_user_count == 0:
                continue
            try:
                user_list = self.get_dept_users(dept.id)
                for u in user_list:
                    if u["id"] not in users:
                        users[u["id"]] = AnyShareUser(
                            id=u["id"],
                            name=u.get("name", ""),
                            email=u.get("email", ""),
                            phone=u.get("phone", u.get("mobile", "")),
                            department_ids=[dept.id],
                        )
                    else:
                        users[u["id"]].department_ids.append(dept.id)
                logger.debug(f"  {dept.name}: {len(user_list)} users")
            except Exception as e:
                logger.warning(f"Failed to get users for {dept.name}: {e}")

        logger.info(f"Total unique users: {len(users)}")
        return list(users.values())
