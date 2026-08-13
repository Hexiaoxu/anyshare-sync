"""Small deterministic AnyShare auth fake used by unit tests."""


class FakeAnyShareAuth:
    def __init__(self):
        self._app_token = "fake-app-token-for-testing"

    def get_app_token(self) -> str:
        return self._app_token

    @staticmethod
    def get_user_token(account: str) -> str:
        return f"fake-user-token-for-{account}"
