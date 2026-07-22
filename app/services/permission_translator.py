"""ACL Translation: AnyShare ACL entries → BISHENG FGA relation tuples.

Core translation rules:
  display + preview + download                → viewer
  above + modify + create                     → editor
  above + delete + internal_sharing           → manager
  is_owner                                    → owner

Blocking conditions (return None = cannot translate):
  - Only preview, no download
  - Has deny entries
  - Expiry time set (BISHENG has no expiry)
  - external_sharing
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.connectors.anyshare.acl import AclEntry
from .principal_mapper import PrincipalMapper, PrincipalType

logger = logging.getLogger(__name__)


@dataclass
class FgaTuple:
    """A single OpenFGA tuple to write."""
    subject_type: str     # "user" | "department" | "user_group"
    subject_id: int       # BISHENG ID
    relation: str         # "viewer" | "editor" | "manager" | "owner"
    object_type: str      # "knowledge_space" | "folder" | "knowledge_file"
    object_id: int        # BISHENG resource ID


@dataclass
class TranslationResult:
    tuples: list[FgaTuple] = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)  # blocked entries with reason


# Allow actions required for each BISHENG level
_REQUIREMENTS = {
    "viewer":  {"display", "preview", "download"},
    "editor":  {"display", "preview", "download", "modify", "create"},
    "manager": {"display", "preview", "download", "modify", "create", "delete", "internal_sharing"},
    "owner":   {"display", "preview", "download", "modify", "create", "delete", "internal_sharing"},
}

# Actions that can never be safely mapped
_UNMAPPABLE = {"external_sharing", "print", "cache"}


class PermissionTranslator:
    """Translates AnyShare ACL → BISHENG FGA tuples."""

    def __init__(self, principal_mapper: PrincipalMapper):
        self._mapper = principal_mapper

    def translate(
        self, entries: list[AclEntry],
        resource_type: str, resource_id: int,
    ) -> TranslationResult:
        """Translate a list of ACL entries into FGA tuples."""
        result = TranslationResult()

        for entry in entries:
            # ── Blocking checks ────────────────────────────
            if entry.deny:
                result.blocked.append({
                    "accessor": entry.accessor_id,
                    "reason": "has_deny_rules",
                    "deny": entry.deny,
                })
                continue

            if entry.expire_time:
                result.blocked.append({
                    "accessor": entry.accessor_id,
                    "reason": "has_expiry",
                    "expire_time": entry.expire_time,
                })
                continue

            # Check for unmappable actions
            for action in _UNMAPPABLE:
                if action in entry.allow:
                    logger.debug(f"Ignoring unmappable action {action} for {entry.accessor_id}")

            # ── Determine relation level ────────────────────
            allowed = set(entry.allow)
            relation = self._determine_relation(allowed)
            if relation is None:
                result.blocked.append({
                    "accessor": entry.accessor_id,
                    "reason": "preview_only_no_download",
                    "allow": entry.allow,
                })
                continue

            # ── Map the accessor to BISHENG ID ──────────────
            subject_id = self._resolve_subject(entry)
            if subject_id is None:
                result.blocked.append({
                    "accessor": entry.accessor_id,
                    "accessor_type": entry.accessor_type,
                    "reason": "subject_not_mapped",
                })
                continue

            # ── Build FGA tuple ─────────────────────────────
            subject_type_map = {
                "user": "user",
                "department": "department",
                "contactor": "user_group",
            }
            result.tuples.append(FgaTuple(
                subject_type=subject_type_map.get(entry.accessor_type, "user"),
                subject_id=subject_id,
                relation=relation,
                object_type=resource_type,
                object_id=resource_id,
            ))

        return result

    def _determine_relation(self, allowed: set[str]) -> str | None:
        """Determine the strongest BISHENG relation from allowed actions.

        Returns None if the minimum viewer requirement (download) is not met.
        """
        if "download" not in allowed:
            return None  # cannot safely grant even viewer

        # Start from highest and work down
        if _REQUIREMENTS["manager"].issubset(allowed):
            return "manager"
        if "owner" in allowed or "delete" in allowed and "internal_sharing" in allowed:
            if _REQUIREMENTS["manager"].issubset(allowed):
                return "manager"
        if _REQUIREMENTS["editor"].issubset(allowed):
            return "editor"
        if _REQUIREMENTS["viewer"].issubset(allowed):
            return "viewer"

        return "viewer"  # safe fallback: has download + at least display/preview

    def _resolve_subject(self, entry: AclEntry) -> int | None:
        """Resolve ACL accessor to BISHENG ID via mapper."""
        mapping = self._mapper.get(entry.accessor_id)
        if mapping is None or mapping.target_id is None:
            return None
        return mapping.target_id
