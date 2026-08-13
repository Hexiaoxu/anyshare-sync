"""BISHENG HTTP client with Cookie-based auth."""

from __future__ import annotations

import base64
import json
import logging
import time
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

    _REFRESH_SKEW_SECONDS = 300

    def __init__(self, base_url: str, cookie_value: str = "", timeout: float = 30.0):
        self._url = base_url.rstrip("/")
        self._timeout = timeout
        if not cookie_value:
            cookie_value = self._generate_token()
        self._cookie = {"access_token_cookie": cookie_value}

    @staticmethod
    def _generate_token() -> str:
        from app.connectors.bisheng.token_generator import generate_bs_token
        return generate_bs_token()

    @staticmethod
    def _token_expiry(token: str) -> int | None:
        """Read JWT exp without verifying it; BISHENG verifies the signature."""
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return int(json.loads(base64.urlsafe_b64decode(payload))["exp"])
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _refresh_token(self, reason: str) -> None:
        self._cookie["access_token_cookie"] = self._generate_token()
        logger.info("BISHENG token refreshed (%s)", reason)

    def _ensure_fresh_token(self) -> None:
        token = self._cookie.get("access_token_cookie", "")
        expiry = self._token_expiry(token)
        if expiry is not None and expiry <= int(time.time()) + self._REFRESH_SKEW_SECONDS:
            self._refresh_token("expiring")

    @staticmethod
    def _is_auth_failure(resp: httpx.Response) -> bool:
        if resp.status_code in (401, 403):
            return True
        try:
            code = resp.json().get("status_code")
            return code in (401, 403)
        except (ValueError, AttributeError):
            return False

    def _request(self, method: str, path: str, *, timeout: float,
                 params: dict | None = None, json_body: dict | None = None,
                 files: dict | None = None, **kwargs) -> httpx.Response:
        """Send a request, refreshing proactively and retrying auth once."""
        self._ensure_fresh_token()
        url = f"{self._url}{path}"
        with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
            response = client.request(
                method, url, params=params, json=json_body, files=files,
                cookies=self._cookie, **kwargs,
            )
            if self._is_auth_failure(response):
                self._refresh_token("authentication failure")
                response = client.request(
                    method, url, params=params, json=json_body, files=files,
                    cookies=self._cookie, **kwargs,
                )
            return response

    # ── Low-level HTTP ──────────────────────────────────────

    def _get(self, path: str, params: dict = None, **kwargs) -> httpx.Response:
        timeout = kwargs.pop("timeout", self._timeout)
        return self._request("GET", path, timeout=timeout,
                             params=params or {}, **kwargs)

    def _post(self, path: str, json_body: dict = None, **kwargs) -> httpx.Response:
        timeout = kwargs.pop("timeout", self._timeout)
        return self._request("POST", path, timeout=timeout,
                             json_body=json_body, **kwargs)

    def _put(self, path: str, json_body: dict = None, **kwargs) -> httpx.Response:
        timeout = kwargs.pop("timeout", self._timeout)
        return self._request("PUT", path, timeout=timeout,
                             json_body=json_body, **kwargs)

    def _delete(self, path: str, **kwargs) -> httpx.Response:
        timeout = kwargs.pop("timeout", self._timeout)
        return self._request("DELETE", path, timeout=timeout, **kwargs)

    def _upload(self, path: str, file_path: str, **kwargs) -> httpx.Response:
        timeout = kwargs.pop("timeout", 120)
        self._ensure_fresh_token()
        with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
            with open(file_path, "rb") as stream:
                response = client.post(
                    f"{self._url}{path}", files={"file": stream},
                    cookies=self._cookie, **kwargs,
                )
            if self._is_auth_failure(response):
                self._refresh_token("authentication failure")
                with open(file_path, "rb") as stream:
                    response = client.post(
                        f"{self._url}{path}", files={"file": stream},
                        cookies=self._cookie, **kwargs,
                    )
            return response

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
