"""Fake AnyShare connector for unit testing.

No network calls — returns pre-configured fixtures.
"""

from __future__ import annotations

from .auth import AnyShareAuth, Token


class FakeAnyShareAuth(AnyShareAuth):
    """Auth that always returns a fake token — no real credentials needed."""

    def __init__(self):
        # Skip real __init__, just set up the fake
        self._app_token = Token(
            access_token="fake-app-token-for-testing",
            expires_at=9999999999.0,
        )

    def get_app_token(self) -> str:
        return self._app_token.access_token

    def get_user_token(self, account: str) -> str:
        return f"fake-user-token-for-{account}"

    def close(self) -> None:
        pass
