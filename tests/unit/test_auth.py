"""Unit tests for AnyShare auth module."""

import pytest
from app.connectors.anyshare.auth import AnyShareAuth, Token
from app.connectors.anyshare.mock import FakeAnyShareAuth


class TestToken:
    def test_not_expired(self):
        t = Token(access_token="x", expires_at=9999999999.0)
        assert not t.is_expired

    def test_expired(self):
        t = Token(access_token="x", expires_at=0.0)  # long ago
        assert t.is_expired

    def test_header_format(self):
        t = Token(access_token="abc", token_type="bearer")
        assert t.header == {"Authorization": "Bearer abc"}


class TestFakeAuth:
    def test_app_token_is_fake(self):
        auth = FakeAnyShareAuth()
        assert auth.get_app_token() == "fake-app-token-for-testing"

    def test_user_token_contains_account_name(self):
        auth = FakeAnyShareAuth()
        token = auth.get_user_token("zhangsan")
        assert "zhangsan" in token

    def test_token_never_expires(self):
        auth = FakeAnyShareAuth()
        # Call twice — should return same cached token
        t1 = auth.get_app_token()
        t2 = auth.get_app_token()
        assert t1 == t2
