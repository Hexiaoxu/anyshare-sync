"""BishengPermission — F048 grants:mutate protocol (context/list/diff/retry).

The client (`self._c`) is faked with a MagicMock whose `.ok()` is scripted
with a side_effect queue matching the exact call order the code makes:
one call per get_context(), one per list_grants() page, one per
grants:mutate batch. `._get`/`._post` themselves don't need real responses
since `ok()` ignores its `resp` arg in these fakes.
"""

from unittest.mock import MagicMock, patch

from app.connectors.bisheng.client import BishengApiError
from app.connectors.bisheng.permission import BishengPermission


def _grant_row(subject_type, subject_id, model_key, assignee_id, version,
               scope="LOCAL", editable=True, protected=False):
    return {
        "assignee_id": assignee_id,
        "assignee_version": version,
        "subject": {"type": subject_type, "id": str(subject_id)},
        "model": {"key": model_key},
        "scope": scope,
        "editable": editable,
        "protected": protected,
    }


def _perm(context=None):
    client = MagicMock()
    perm = BishengPermission(client)
    return perm, client


def _ctx(resource_version=1, catalog_release_id=1):
    """What client.ok(resp) returns for a GET .../context call — ok() returns
    the full response body, and BISHENG puts the context fields straight in
    its top-level "data"."""
    return {"data": {"resource_version": resource_version,
                     "catalog_release_id": catalog_release_id}}


def _grants_page(rows, next_cursor=None):
    """What client.ok(resp) returns for a GET .../grants call — the payload
    itself has its own "data" key (the rows), nested under ok()'s "data"."""
    return {"data": {"data": rows, "next_cursor": next_cursor}}


# ── sync_grants: diffing ─────────────────────────────────────

def test_sync_grants_adds_when_nothing_granted_yet():
    perm, client = _perm()
    client.ok.side_effect = [_ctx(), _grants_page([]), {}]  # context, grants, mutate
    desired = [{"subject_type": "user", "subject_id": 7, "relation": "viewer"}]

    ok = perm.sync_grants("folder", 100, desired)

    assert ok is True
    mutate_call = client._post.call_args_list[0]
    assert mutate_call.args[0] == "/api/v1/permissions/resources/folder/100/grants:mutate"
    body = mutate_call.args[1]
    assert body["expected_resource_version"] == 1
    assert body["expected_catalog_release_id"] == 1
    assert body["changes"] == [
        {"op": "ADD", "model_key": "viewer", "subject": {"type": "user", "id": "7"}}
    ]


def test_sync_grants_removes_live_grant_missing_from_desired():
    perm, client = _perm()
    live = [_grant_row("user", 9, "viewer", assignee_id="a1", version=3)]
    client.ok.side_effect = [_ctx(), _grants_page(live), {}]

    ok = perm.sync_grants("folder", 100, desired=[])

    assert ok is True
    body = client._post.call_args_list[0].args[1]
    assert body["changes"] == [
        {"op": "REMOVE", "assignee_id": "a1", "expected_assignee_version": 3}
    ]


def test_sync_grants_noop_when_live_matches_desired():
    perm, client = _perm()
    live = [_grant_row("user", 9, "viewer", assignee_id="a1", version=3)]
    client.ok.side_effect = [_ctx(), _grants_page(live)]  # no mutate call expected
    desired = [{"subject_type": "user", "subject_id": 9, "relation": "viewer"}]

    ok = perm.sync_grants("folder", 100, desired)

    assert ok is True
    client._post.assert_not_called()


def test_sync_grants_ignores_inherited_and_protected_rows():
    """INHERITED and protected (e.g. creator-owner) rows must never be
    targeted for REMOVE just because they're absent from `desired`."""
    perm, client = _perm()
    live = [
        _grant_row("user", 1, "viewer", "a1", 1, scope="INHERITED"),
        _grant_row("user", 2, "owner", "a2", 1, protected=True),
        _grant_row("user", 3, "viewer", "a3", 1, editable=False),
    ]
    client.ok.side_effect = [_ctx(), _grants_page(live)]

    ok = perm.sync_grants("folder", 100, desired=[])

    assert ok is True
    client._post.assert_not_called()


def test_sync_grants_include_children_passed_through():
    perm, client = _perm()
    client.ok.side_effect = [_ctx(), _grants_page([]), {}]
    desired = [{"subject_type": "department", "subject_id": 5,
                "relation": "viewer", "include_children": True}]

    perm.sync_grants("folder", 100, desired)

    body = client._post.call_args_list[0].args[1]
    assert body["changes"][0]["subject"] == {
        "type": "department", "id": "5", "include_children": True,
    }


# ── add_grant: additive, idempotent ──────────────────────────

def test_add_grant_noop_when_already_granted():
    perm, client = _perm()
    live = [_grant_row("user", 7, "owner", "a1", 1)]
    client.ok.side_effect = [_ctx(), _grants_page(live)]

    ok = perm.add_grant("knowledge_space", 1, "user", 7, "owner")

    assert ok is True
    client._post.assert_not_called()


def test_add_grant_leaves_other_grants_untouched():
    perm, client = _perm()
    live = [_grant_row("user", 1, "viewer", "a1", 1)]  # unrelated existing grant
    client.ok.side_effect = [_ctx(), _grants_page(live), {}]

    ok = perm.add_grant("knowledge_space", 1, "user", 7, "owner")

    assert ok is True
    body = client._post.call_args_list[0].args[1]
    assert body["changes"] == [
        {"op": "ADD", "model_key": "owner", "subject": {"type": "user", "id": "7"}}
    ]


# ── retry on version conflict ────────────────────────────────

@patch("app.connectors.bisheng.permission.time.sleep")
def test_sync_grants_retries_on_version_conflict(_sleep):
    perm, client = _perm()
    conflict = BishengApiError(25002, "Permission data version conflict")
    client.ok.side_effect = [
        _ctx(resource_version=1), _grants_page([]), conflict,        # attempt 1: conflict
        _ctx(resource_version=2), _grants_page([]), {},              # attempt 2: succeeds
    ]
    desired = [{"subject_type": "user", "subject_id": 7, "relation": "viewer"}]

    ok = perm.sync_grants("folder", 100, desired, retries=2)

    assert ok is True
    assert client._post.call_count == 2
    assert client._post.call_args_list[1].args[1]["expected_resource_version"] == 2


def test_sync_grants_does_not_retry_non_conflict_business_error():
    perm, client = _perm()
    rejected = BishengApiError(400, "invalid subject")
    client.ok.side_effect = [_ctx(), _grants_page([]), rejected]
    desired = [{"subject_type": "user", "subject_id": 7, "relation": "viewer"}]

    ok = perm.sync_grants("folder", 100, desired, retries=2)

    assert ok is False
    assert client._post.call_count == 1  # no retry burned on a non-retryable error


# ── batching ──────────────────────────────────────────────────

def test_sync_grants_batches_changes_and_bumps_version_between_batches():
    perm, client = _perm()
    desired = [
        {"subject_type": "user", "subject_id": i, "relation": "viewer"}
        for i in range(60)  # > _MUTATE_BATCH_SIZE (50)
    ]
    client.ok.side_effect = [_ctx(resource_version=5), _grants_page([]), {}, {}]

    ok = perm.sync_grants("folder", 100, desired)

    assert ok is True
    assert client._post.call_count == 2
    first, second = (c.args[1] for c in client._post.call_args_list)
    assert len(first["changes"]) == 50
    assert len(second["changes"]) == 10
    assert first["expected_resource_version"] == 5
    assert second["expected_resource_version"] == 6  # bumped after batch 1 succeeded


# ── search_grant_users ───────────────────────────────────────
#
# Confirmed live against a real F048 BISHENG instance: the endpoint's
# payload is {"data": rows, "total": N} nested under ok()'s own "data"
# envelope, so a single .get("data", []) returns that wrapper dict, not
# the rows — and principal_mapper.py's `for u in results: u.get(...)`
# would then iterate the dict's string keys and blow up on `.get`,
# silently swallowed by its broad `except Exception`. Every live user
# lookup was failing before this fix.

def test_search_grant_users_unwraps_double_nested_response():
    perm, client = _perm()
    rows = [{"user_id": 7, "user_name": "alice"}]
    client.ok.return_value = {"data": {"data": rows, "total": 1}}

    result = perm.search_grant_users(1, "ali")

    assert result == rows


# ── list_grants pagination ───────────────────────────────────

def test_list_grants_follows_cursor():
    perm, client = _perm()
    page1 = _grants_page([_grant_row("user", 1, "viewer", "a1", 1)], next_cursor="c2")
    page2 = _grants_page([_grant_row("user", 2, "viewer", "a2", 1)], next_cursor=None)
    client.ok.side_effect = [page1, page2]

    rows = perm.list_grants("folder", 100)

    assert len(rows) == 2
    assert client._get.call_count == 2
    assert "cursor" not in client._get.call_args_list[0].kwargs.get("params", {})
    assert client._get.call_args_list[1].kwargs["params"]["cursor"] == "c2"
