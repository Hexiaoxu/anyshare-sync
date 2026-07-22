"""BISHENG folder operations within knowledge spaces."""

from __future__ import annotations

import logging

from .client import BishengClient

logger = logging.getLogger(__name__)


class BishengFolder:
    """Create, list, rename, delete folders."""

    def __init__(self, client: BishengClient):
        self._c = client

    def create(self, space_id: int, name: str, parent_id: int | None = None) -> int:
        """Create a folder. Returns folder_id (KnowledgeFile.id)."""
        body = {"name": name}
        if parent_id is not None:
            body["parent_id"] = parent_id
        resp = self._c._post(f"/api/v1/knowledge/space/{space_id}/folders", body)
        return self._c.extract_id(self._c.ok(resp))

    def list_children(self, space_id: int, parent_id: int | None = None,
                      page: int = 1, page_size: int = 50) -> dict:
        """List direct children of a folder (or root)."""
        params = {"page": page, "page_size": page_size}
        if parent_id is not None:
            params["parent_id"] = parent_id
        resp = self._c._get(f"/api/v1/knowledge/space/{space_id}/children", params)
        return self._c.ok(resp)["data"]

    def rename(self, space_id: int, folder_id: int, new_name: str) -> None:
        body = {"name": new_name}
        self._c._put(f"/api/v1/knowledge/space/{space_id}/folders/{folder_id}", body)

    def delete(self, space_id: int, folder_id: int) -> None:
        self._c._delete(f"/api/v1/knowledge/space/{space_id}/folders/{folder_id}")

    def get_parent_chain(self, space_id: int, folder_id: int) -> list[dict]:
        """Get breadcrumb path from root to this folder."""
        resp = self._c._get(f"/api/v1/knowledge/space/{space_id}/folders/{folder_id}/parent")
        return self._c.ok(resp)["data"]
