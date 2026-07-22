"""BISHENG permission management — OpenFGA-based ReBAC."""

from __future__ import annotations

import logging
import time

from .client import BishengClient, BishengApiError

logger = logging.getLogger(__name__)


class BishengPermission:
    """Grant, revoke, and list permissions via BISHENG ReBAC API.

    Uses the v2.6.0 authorize API: POST /api/v1/permissions/resources/{type}/{id}/authorize
    Request body: {"grants": [...], "revokes": [...]}
    """

    # Supported grant subject types
    SUBJECT_USER = "user"
    SUBJECT_DEPARTMENT = "department"
    SUBJECT_USER_GROUP = "user_group"

    # Supported relations (maps to AnyShare ACL levels)
    RELATION_VIEWER = "viewer"
    RELATION_EDITOR = "editor"
    RELATION_MANAGER = "manager"
    RELATION_OWNER = "owner"

    # Valid resource types
    RESOURCE_SPACE = "knowledge_space"
    RESOURCE_FOLDER = "folder"
    RESOURCE_FILE = "knowledge_file"

    def __init__(self, client: BishengClient):
        self._c = client

    # ── Batch API (v2.6.0 format) ──────────────────────────

    def authorize(self, resource_type: str, resource_id: int,
                  grants: list[dict] | None = None,
                  revokes: list[dict] | None = None,
                  timeout: float = 60.0,
                  retries: int = 2) -> bool:
        """Grant and/or revoke permissions in a single call.

        Args:
            resource_type: one of RESOURCE_* constants
            resource_id: BISHENG resource ID
            grants: list of {"subject_type", "subject_id", "relation", "include_children"?}
            revokes: list of {"subject_type", "subject_id", "relation"}
            timeout: request timeout (authorize writes to OpenFGA, can be slow)
            retries: number of retries on timeout

        Returns True on success, False on failure.
        """
        body = {
            "grants": grants or [],
            "revokes": revokes or [],
        }
        for attempt in range(retries + 1):
            try:
                resp = self._c._post(
                    f"/api/v1/permissions/resources/{resource_type}/{resource_id}/authorize",
                    body,
                    timeout=timeout,
                )
                self._c.ok(resp)
                return True
            except BishengApiError as e:
                # Business error — don't retry
                logger.error(f"authorize rejected: {e.message}")
                return False
            except Exception as e:
                if attempt < retries:
                    logger.debug(f"authorize retry {attempt+1}: {e}")
                    time.sleep(3)
                else:
                    logger.error(f"authorize failed after {retries} retries: {e}")
                    return False
        return False

    # ── Convenience: single grant ───────────────────────────

    def grant(self, resource_type: str, resource_id: int,
              subject_type: str, subject_id: int,
              relation: str,
              include_children: bool = False) -> bool:
        """Grant a single permission. Idempotent."""
        grant = {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "relation": relation,
        }
        if include_children is not None:
            grant["include_children"] = include_children
        return self.authorize(resource_type, resource_id, grants=[grant])

    def revoke(self, resource_type: str, resource_id: int,
               subject_type: str, subject_id: int,
               relation: str) -> bool:
        """Revoke a single permission."""
        revoke = {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "relation": relation,
        }
        return self.authorize(resource_type, resource_id, revokes=[revoke])

    # ── Read ────────────────────────────────────────────────

    def list_permissions(self, resource_type: str, resource_id: int) -> list[dict]:
        """List all permissions on a resource."""
        resp = self._c._get(
            f"/api/v1/permissions/resources/{resource_type}/{resource_id}/permissions",
        )
        return self._c.ok(resp).get("data", [])

    # ── User / department picker helpers ────────────────────

    def search_grant_users(self, space_id: int, keyword: str, page_size: int = 10) -> list[dict]:
        """Search BISHENG users available for permission grants (prefix match)."""
        resp = self._c._get(
            f"/api/v1/permissions/resources/knowledge_space/{space_id}/grant-subjects/users",
            params={"keyword": keyword, "page": 1, "page_size": page_size},
        )
        return self._c.ok(resp).get("data", [])

    def search_grant_departments(self, space_id: int, keyword: str, limit: int = 10) -> list[dict]:
        """Search BISHENG departments available for permission grants."""
        resp = self._c._get(
            f"/api/v1/permissions/resources/knowledge_space/{space_id}/grant-subjects/departments/search",
            params={"keyword": keyword, "limit": limit},
        )
        data = self._c.ok(resp).get("data", {})
        # Return flat list of matched departments from the tree
        results = []

        def _flatten(nodes):
            for n in nodes:
                if n.get("matched"):
                    results.append(n)
                if n.get("children"):
                    _flatten(n["children"])

        _flatten(data.get("roots", []))
        return results

    def list_space_members(self, space_id: int) -> list[dict]:
        """List members of a knowledge space."""
        resp = self._c._get(f"/api/v1/knowledge/space/{space_id}/members")
        data = self._c.ok(resp)
        return data.get("data", data.get("data", []))
