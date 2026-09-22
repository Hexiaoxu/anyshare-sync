"""BISHENG permission management — F048 Catalog/Grant permission model.

BISHENG 3.0 (F048) retired the old ReBAC-style batch
``POST .../authorize`` endpoint (grants+revokes in one call, integer
subject IDs) in favor of an optimistic-concurrency Catalog/Grant model:

    GET  .../context        -> {resource_version, catalog_release_id}
    GET  .../grants          -> paginated live grant-assignee rows
    POST .../grants:mutate   -> {idempotency_key, expected_resource_version,
                                  expected_catalog_release_id, changes: [...]}

There is no compat shim for the old endpoint (BISHENG's own
test_f048_legacy_api_retirement.py asserts it's unreachable), so every
caller here must read current state (context + live grants) before
mutating, and retry on a version conflict (business error code 25002)
by re-reading and rebuilding the change set from scratch.

The four standard models are named identically to the old relations —
viewer/editor/manager/owner — so callers can keep using those names;
this module treats them as the F048 ``model_key``.
"""

from __future__ import annotations

import logging
import time
import uuid

from .client import BishengClient, BishengApiError

logger = logging.getLogger(__name__)


class BishengPermission:
    """Grant, revoke, and list permissions via BISHENG's F048 Grant API."""

    # Supported grant subject types
    SUBJECT_USER = "user"
    SUBJECT_DEPARTMENT = "department"
    SUBJECT_USER_GROUP = "user_group"

    # Standard model keys (== old ReBAC relation names)
    RELATION_VIEWER = "viewer"
    RELATION_EDITOR = "editor"
    RELATION_MANAGER = "manager"
    RELATION_OWNER = "owner"

    # Valid resource types
    RESOURCE_SPACE = "knowledge_space"
    RESOURCE_FOLDER = "folder"
    RESOURCE_FILE = "knowledge_file"

    # PermissionVersionConflictError — expected_resource_version or
    # expected_catalog_release_id went stale under us; retry is safe.
    _VERSION_CONFLICT_CODE = 25002

    _MUTATE_BATCH_SIZE = 50  # GrantMutationRequest.changes max_length

    def __init__(self, client: BishengClient):
        self._c = client

    # ── Read ────────────────────────────────────────────────

    def get_context(self, resource_type: str, resource_id: int) -> dict:
        """Fetch {mode, resource_version, catalog_release_id, ...} for a resource."""
        resp = self._c._get(
            f"/api/v1/permissions/resources/{resource_type}/{resource_id}/context",
        )
        return self._c.ok(resp).get("data", {})

    def list_grants(self, resource_type: str, resource_id: int,
                     page_size: int = 200) -> list[dict]:
        """All grant-assignee rows (LOCAL + INHERITED) on a resource, paginated."""
        items: list[dict] = []
        cursor = None
        while True:
            params = {"page_size": page_size}
            if cursor:
                params["cursor"] = cursor
            resp = self._c._get(
                f"/api/v1/permissions/resources/{resource_type}/{resource_id}/grants",
                params=params,
            )
            page = self._c.ok(resp).get("data", {})
            items.extend(page.get("data", []))
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return items

    # ── Write ───────────────────────────────────────────────

    def sync_grants(self, resource_type: str, resource_id: int,
                     desired: list[dict], timeout: float = 60.0,
                     retries: int = 2) -> bool:
        """Reconcile a resource's LOCAL grants to exactly `desired`.

        desired: [{"subject_type", "subject_id", "relation",
                    "include_children"?}, ...]  (relation == model_key)

        Diffs against BISHENG's current LOCAL, editable, non-protected
        grants and issues ADD/REMOVE changes via grants:mutate. Anything
        missing from `desired` gets revoked — pass [] to fully revoke a
        resource. Returns True on success (including a true no-op).
        """
        desired_keys = {
            (g["subject_type"], str(g["subject_id"]), g["relation"])
            for g in desired
        }

        def build(live_local: dict[tuple, dict]) -> list[dict]:
            changes = []
            for g in desired:
                key = (g["subject_type"], str(g["subject_id"]), g["relation"])
                if key in live_local:
                    continue
                subject = {"type": g["subject_type"], "id": str(g["subject_id"])}
                if g.get("include_children") is not None:
                    subject["include_children"] = g["include_children"]
                changes.append({"op": "ADD", "model_key": g["relation"], "subject": subject})
            for key, row in live_local.items():
                if key not in desired_keys:
                    changes.append({
                        "op": "REMOVE",
                        "assignee_id": row["assignee_id"],
                        "expected_assignee_version": row["assignee_version"],
                    })
            return changes

        return self._apply(resource_type, resource_id, build, timeout, retries)

    def add_grant(self, resource_type: str, resource_id: int,
                  subject_type: str, subject_id: int, relation: str,
                  include_children: bool | None = None,
                  timeout: float = 60.0, retries: int = 2) -> bool:
        """Idempotent single additive grant — leaves other grants untouched."""
        key = (subject_type, str(subject_id), relation)

        def build(live_local: dict[tuple, dict]) -> list[dict]:
            if key in live_local:
                return []
            subject = {"type": subject_type, "id": str(subject_id)}
            if include_children is not None:
                subject["include_children"] = include_children
            return [{"op": "ADD", "model_key": relation, "subject": subject}]

        return self._apply(resource_type, resource_id, build, timeout, retries)

    # ── Internals ───────────────────────────────────────────

    def _read_state(self, resource_type: str, resource_id: int) -> tuple[dict, dict]:
        """(context, {(subject_type, subject_id, model_key): grant_row}) for
        LOCAL, editable, non-protected grants — the ones a caller may add to
        or remove from without touching inherited/system/protected-owner rows."""
        ctx = self.get_context(resource_type, resource_id)
        live = self.list_grants(resource_type, resource_id)
        live_local = {
            (g["subject"]["type"], str(g["subject"]["id"]), g["model"]["key"]): g
            for g in live
            if g.get("scope") == "LOCAL" and g.get("editable") and not g.get("protected")
        }
        return ctx, live_local

    def _apply(self, resource_type: str, resource_id: int, build_changes,
               timeout: float, retries: int) -> bool:
        """Read state, build changes, POST grants:mutate in <=50-change
        batches, and retry the whole read+build+apply cycle on a version
        conflict or transient error. `build_changes(live_local) -> list[dict]`."""
        for attempt in range(retries + 1):
            try:
                ctx, live_local = self._read_state(resource_type, resource_id)
            except Exception as e:
                if attempt < retries:
                    logger.debug(f"permission read retry {attempt + 1}: {e}")
                    time.sleep(3)
                    continue
                logger.error(f"permission sync failed to read resource state: {e}")
                return False

            changes = build_changes(live_local)
            if not changes:
                return True

            resource_version = ctx["resource_version"]
            catalog_release_id = ctx["catalog_release_id"]
            success, retryable = True, True
            for i in range(0, len(changes), self._MUTATE_BATCH_SIZE):
                batch = changes[i:i + self._MUTATE_BATCH_SIZE]
                body = {
                    "idempotency_key": uuid.uuid4().hex,
                    "expected_resource_version": resource_version,
                    "expected_catalog_release_id": catalog_release_id,
                    "changes": batch,
                }
                try:
                    resp = self._c._post(
                        f"/api/v1/permissions/resources/{resource_type}/{resource_id}/grants:mutate",
                        body, timeout=timeout,
                    )
                    self._c.ok(resp)
                    resource_version += 1  # each successful mutate bumps it by exactly 1
                except BishengApiError as e:
                    success = False
                    retryable = (e.code == self._VERSION_CONFLICT_CODE)
                    if not retryable:
                        logger.error(f"grants:mutate rejected: {e.message}")
                    break
                except Exception as e:
                    success, retryable = False, True
                    logger.debug(f"grants:mutate transport error: {e}")
                    break

            if success:
                return True
            if not retryable:
                return False
            if attempt < retries:
                logger.debug(f"permission sync retry {attempt + 1} after mutate failure")
                time.sleep(2)
                continue
            logger.error(f"permission sync failed after {retries} retries")
            return False
        return False

    # ── User / department picker helpers ────────────────────

    def search_grant_users(self, space_id: int, keyword: str, page_size: int = 10) -> list[dict]:
        """Search BISHENG users available for permission grants (prefix match)."""
        resp = self._c._get(
            f"/api/v1/permissions/resources/knowledge_space/{space_id}/grant-subjects/users",
            params={"keyword": keyword, "page": 1, "page_size": page_size},
        )
        # Response payload is {"data": rows, "total": N} nested under ok()'s
        # own "data" envelope — a plain .get("data", []) here would return
        # that wrapper dict instead of the rows list.
        return self._c.ok(resp).get("data", {}).get("data", [])

    def search_grant_departments(self, space_id: int, keyword: str, limit: int = 10) -> list[dict]:
        """Search BISHENG departments available for permission grants."""
        resp = self._c._get(
            f"/api/v1/permissions/resources/knowledge_space/{space_id}/grant-subjects/departments/search",
            params={"keyword": keyword, "limit": limit},
        )
        data = self._c.ok(resp).get("data", {})
        # Return flat list of matched departments from the tree
        results = []

        def _flatten(nodes):
            for n in nodes:
                if n.get("matched"):
                    results.append(n)
                if n.get("children"):
                    _flatten(n["children"])

        _flatten(data.get("roots", []))
        return results

    def list_space_members(self, space_id: int) -> list[dict]:
        """List members of a knowledge space."""
        resp = self._c._get(f"/api/v1/knowledge/space/{space_id}/members")
        data = self._c.ok(resp)
        return data.get("data", data.get("data", []))
