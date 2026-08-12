"""BISHENG knowledge space operations."""

from __future__ import annotations

import logging

from .client import BishengClient

logger = logging.getLogger(__name__)


class BishengSpace:
    """Create, query, delete knowledge spaces."""

    def __init__(self, client: BishengClient):
        self._c = client

    def create_personal(self, name: str, description: str = "") -> int:
        """Create a personal knowledge space. Returns space_id."""
        resp = self._c._post("/api/v1/knowledge/space", {
            "name": name,
            "description": description,
            "auth_type": "public",
        })
        data = self._c.ok(resp)
        return self._c.extract_id(data)

    def create_organization(self, name: str, description: str = "") -> int:
        """Create an organization knowledge space."""
        resp = self._c._post("/api/v1/knowledge/space", {
            "name": name,
            "description": description,
            "auth_type": "public",
        })
        return self._c.extract_id(self._c.ok(resp))

    def get_info(self, space_id: int) -> dict:
        resp = self._c._get(f"/api/v1/knowledge/space/{space_id}/info")
        return self._c.ok(resp)["data"]

    def list_mine(self) -> list[dict]:
        resp = self._c._get("/api/v1/knowledge/space/mine")
        return self._c.ok(resp)["data"]

    def delete(self, space_id: int) -> None:
        self._c._delete(f"/api/v1/knowledge/space/{space_id}")

    def subscribe(self, space_id: int) -> dict:
        resp = self._c._post(f"/api/v1/knowledge/space/{space_id}/subscribe")
        return self._c.ok(resp)["data"]

    def list_members(self, space_id: int) -> list[dict]:
        resp = self._c._get(f"/api/v1/knowledge/space/{space_id}/members")
        return self._c.ok(resp)["data"].get("data", [])

    def cleanup_by_name(self, name: str) -> int:
        """Delete all spaces whose name contains `name`. Returns count of deleted spaces."""
        deleted = 0
        for sp in self.list_mine():
            if name in sp.get("name", ""):
                try:
                    self.delete(sp["id"])
                    deleted += 1
                    logger.info(f"Cleaned up old space: {sp['name']} (id={sp['id']})")
                except Exception as e:
                    logger.warning(f"Failed to delete space {sp['name']}: {e}")
        return deleted
