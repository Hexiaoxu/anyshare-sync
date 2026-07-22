"""Test AnyShare error classification."""

from app.connectors.anyshare.exceptions import (
    AuthError, AccessLost, NotFound, RateLimited,
    ServerError, classify_status,
)


def test_classify_401():
    assert classify_status(401) == AuthError


def test_classify_403():
    assert classify_status(403) == AccessLost


def test_classify_404():
    assert classify_status(404) == NotFound


def test_classify_429():
    assert classify_status(429) == RateLimited


def test_classify_500():
    assert classify_status(500) == ServerError


def test_classify_502():
    assert classify_status(502) == ServerError
