"""AnyShare OAuth2 authentication.

Two token types:
- app_token  (client_credentials) — for org, doclib, ACL APIs.
- user_token (account-based)    — for personal doclib, download APIs.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass

import httpx

from .exceptions import AuthError, NetworkError

logger = logging.getLogger(__name__)


@dataclass
class Token:
    access_token: str
    token_type: str = "bearer"
    expires_at: float = 0.0

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at - 60  # 60s buffer

    @property
    def header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


class AnyShareAuth:
    """Manages AnyShare OAuth2 app and user tokens with caching."""

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        timeout: float = 30.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._http = httpx.Client(timeout=httpx.Timeout(timeout))
        self._app_token: Token | None = None
        self._user_tokens: dict[str, Token] = {}  # account -> token cache

    @property
    def _basic_auth(self) -> str:
        """Build Basic auth header value."""
        raw = f"{self._client_id}:{self._client_secret}"
        encoded = base64.b64encode(raw.encode()).decode()
        return f"Basic {encoded}"

    # ── App Token ────────────────────────────────────────────

    def get_app_token(self) -> str:
        """Get a valid app token (client_credentials), refreshing if needed."""
        if self._app_token is None or self._app_token.is_expired:
            self._app_token = self._fetch_app_token()
        return self._app_token.access_token

    def _fetch_app_token(self) -> Token:
        url = f"{self._base_url}/oauth2/token"
        data = {"grant_type": "client_credentials", "scope": "all"}
        headers = {
            "Authorization": self._basic_auth,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        for attempt in range(3):
            try:
                resp = self._http.post(url, data=data, headers=headers)
                if resp.status_code in (502, 503, 429):
                    import time as _t
                    _t.sleep(5 * (attempt + 1))
                    continue
                self._raise_on_error(resp)
                body = resp.json()
                expires_in = body.get("expires_in", 3600)
                return Token(
                    access_token=body["access_token"],
                    token_type=body.get("token_type", "bearer"),
                    expires_at=time.time() + expires_in,
                )
            except httpx.TimeoutException:
                raise NetworkError("Timeout fetching app token")
        raise NetworkError("Auth server error: 502/503 after retries")

    # ── User Token ───────────────────────────────────────────

    def get_user_token(self, account: str) -> str:
        """Get a valid user token for *account*, refreshing if needed."""
        cached = self._user_tokens.get(account)
        if cached is not None and not cached.is_expired:
            return cached.access_token
        token = self._fetch_user_token(account)
        self._user_tokens[account] = token
        return token.access_token

    def _fetch_user_token(self, account: str) -> Token:
        url = f"{self._base_url}/api/authentication/v1/access_token"
        headers = {
            "Authorization": self._basic_auth,
            "Content-Type": "application/json",
        }
        body = {"account": account}
        for attempt in range(3):
            try:
                resp = self._http.post(url, json=body, headers=headers)
                if resp.status_code in (502, 503, 429):
                    import time as _t
                    _t.sleep(5 * (attempt + 1))
                    continue
                self._raise_on_error(resp)
                data = resp.json()
                expires_in = data.get("expires_in", 3600)
                return Token(
                    access_token=data["access_token"],
                    token_type=data.get("token_type", "bearer"),
                    expires_at=time.time() + expires_in,
                )
            except httpx.TimeoutException:
                raise NetworkError(f"Timeout fetching user token for {account}")
        raise NetworkError(f"Auth server error: 502/503 after retries for {account}")

    # ── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _raise_on_error(resp: httpx.Response) -> None:
        if resp.status_code == 401:
            raise AuthError("Invalid client credentials")
        if 500 <= resp.status_code < 600:
            raise NetworkError(f"Auth server error: {resp.status_code}")

    def close(self) -> None:
        self._http.close()
