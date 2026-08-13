"""BISHENG JWT refresh and retry behavior."""

import base64
import json
import time
from unittest.mock import MagicMock, patch

import httpx

from app.connectors.bisheng.client import BishengClient


def _unsigned_token(exp: int) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": exp}).encode()
    ).rstrip(b"=").decode()
    return f"header.{payload}.signature"


def _response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(
        status, json=body,
        request=httpx.Request("GET", "http://bisheng.test/api"),
    )


def test_refreshes_before_expiry():
    old = _unsigned_token(int(time.time()) + 60)
    new = _unsigned_token(int(time.time()) + 3600)
    client = BishengClient("http://bisheng.test", old)
    http = MagicMock()
    http.request.return_value = _response(200, {"status_code": 200})
    http.__enter__.return_value = http
    http.__exit__.return_value = False

    with patch.object(client, "_generate_token", return_value=new), \
         patch("app.connectors.bisheng.client.httpx.Client", return_value=http):
        client._get("/api")

    assert client._cookie["access_token_cookie"] == new
    assert http.request.call_count == 1


def test_auth_failure_refreshes_and_retries_once():
    old = _unsigned_token(int(time.time()) + 3600)
    new = _unsigned_token(int(time.time()) + 7200)
    client = BishengClient("http://bisheng.test", old)
    http = MagicMock()
    http.request.side_effect = [
        _response(200, {"status_code": 401}),
        _response(200, {"status_code": 200}),
    ]
    http.__enter__.return_value = http
    http.__exit__.return_value = False

    with patch.object(client, "_generate_token", return_value=new), \
         patch("app.connectors.bisheng.client.httpx.Client", return_value=http):
        response = client._get("/api")

    assert response.json()["status_code"] == 200
    assert client._cookie["access_token_cookie"] == new
    assert http.request.call_count == 2

