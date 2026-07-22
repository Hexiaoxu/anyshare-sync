"""AnyShare document library discovery."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import httpx

logger = logging.getLogger(__name__)


class DocLibType(str, Enum):
    USER = "user_doc_lib"
    DEPARTMENT = "department_doc_lib"
    KNOWLEDGE = "knowledge_doc_lib"


@dataclass
class DocLib:
    id: str          # GNS id
    name: str
    type: DocLibType
    owner_id: str | None = None
    owner_name: str | None = None
    created_at: str | None = None


class AnyShareDocLib:
    """Discovers personal, department, and knowledge doc libs."""

    def __init__(self, base_url: str, get_app_token, get_user_token, timeout: float = 30.0):
        self._url = base_url.rstrip("/")
        self._get_app_token = get_app_token
        self._get_user_token = get_user_token
        self._timeout = timeout

    def _list(self, path: str, token: str, params: dict = None) -> list[dict]:
        with httpx.Client(timeout=httpx.Timeout(self._timeout)) as client:
            resp = client.get(
                f"{self._url}{path}",
                params=params or {},
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("entries", data.get("doc_libs", []))

    def list_personal(self, account: str | None = None) -> list[DocLib]:
        """List personal doc libs. Uses user token if account is given."""
        token = self._get_user_token(account) if account else self._get_app_token()
        items = self._list("/api/efast/v1/doc-lib/user", token, {"offset": 0, "limit": 1000})
        return [self._to_doclib(item, DocLibType.USER) for item in items]

    def list_department(self) -> list[DocLib]:
        items = self._list(
            "/api/efast/v1/doc-lib/department",
            self._get_app_token(),
            {"offset": 0, "limit": 1000},
        )
        return [self._to_doclib(item, DocLibType.DEPARTMENT) for item in items]

    def list_knowledge(self) -> list[DocLib]:
        items = self._list(
            "/api/efast/v1/doc-lib/knowledge",
            self._get_app_token(),
            {"offset": 0, "limit": 1000},
        )
        return [self._to_doclib(item, DocLibType.KNOWLEDGE) for item in items]

    @staticmethod
    def _to_doclib(item: dict, lib_type: DocLibType) -> DocLib:
        owner = item.get("owned_by", [{}])[0] if item.get("owned_by") else {}
        return DocLib(
            id=item["id"],
            name=item.get("name", ""),
            type=lib_type,
            owner_id=owner.get("id"),
            owner_name=owner.get("name"),
            created_at=item.get("created_at"),
        )
