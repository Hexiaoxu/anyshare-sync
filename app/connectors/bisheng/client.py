"""BISHENG HTTP client with Cookie-based auth."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class BishengApiError(Exception):
    """BISHENG business-level API error (HTTP 200 but status_code != 200)."""
    def __init__(self, code: int, message: str, data: dict | None = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"BISHENG API error {code}: {message}")


class BishengClient:
    """HTTP client for BISHENG API.

    Auth: Cookie-based (access_token_cookie JWT).
    If cookie_value is empty, auto-generates an admin JWT.
    IMPORTANT: BISHENG always returns HTTP 200 — real success/failure
    is in response body's ``status_code`` field.
    """

    def __init__(self, base_url: str, cookie_value: str = "", timeout: float = 30.0):
        self._url = base_url.rstrip("/")
        self._timeout = timeout
        if not cookie_value:
            from app.connectors.bisheng.token_generator import generate_bs_token
            cookie_value = generate_bs_token()
        self._cookie = {"access_token_cookie": cookie_value}

    # ── Low-level HTTP ──────────────────────────────────────

    def _get(self, path: str, params: dict = None, **kwargs) -> httpx.Response:
        timeout = kwargs.pop("timeout", self._timeout)
        with httpx.Client(timeout=httpx.Timeout(timeout)) as c:
            return c.get(
                f"{self._url}{path}",
                params=params or {},
                cookies=self._cookie,
                **kwargs,
            )

    def _post(self, path: str, json_body: dict = None, **kwargs) -> httpx.Response:
        timeout = kwargs.pop("timeout", self._timeout)
        with httpx.Client(timeout=httpx.Timeout(timeout)) as c:
            return c.post(
                f"{self._url}{path}",
                json=json_body,
                cookies=self._cookie,
                **kwargs,
            )

    def _put(self, path: str, json_body: dict = None, **kwargs) -> httpx.Response:
        timeout = kwargs.pop("timeout", self._timeout)
        with httpx.Client(timeout=httpx.Timeout(timeout)) as c:
            return c.put(
                f"{self._url}{path}",
                json=json_body,
                cookies=self._cookie,
                **kwargs,
            )

    def _delete(self, path: str, **kwargs) -> httpx.Response:
        timeout = kwargs.pop("timeout", self._timeout)
        with httpx.Client(timeout=httpx.Timeout(timeout)) as c:
            return c.delete(
                f"{self._url}{path}",
                cookies=self._cookie,
                **kwargs,
            )

    def _upload(self, path: str, file_path: str, **kwargs) -> httpx.Response:
        timeout = kwargs.pop("timeout", 120)
        with httpx.Client(timeout=httpx.Timeout(timeout)) as c:
            with open(file_path, "rb") as f:
                return c.post(
                    f"{self._url}{path}",
                    files={"file": f},
                    cookies=self._cookie,
                    **kwargs,
                )

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def ok(resp: httpx.Response) -> dict:
        """Check response. BISHENG always returns HTTP 200 — real status
        is in body's ``status_code`` field (200 = SUCCESS)."""
        resp.raise_for_status()
        data = resp.json()
        biz_code = data.get("status_code", 200)
        if biz_code != 200:
            msg = data.get("status_message", "unknown error")
            raise BishengApiError(biz_code, msg, data)
        return data

    @staticmethod
    def extract_id(resp_data: dict) -> int:
        return resp_data["data"]["id"]
