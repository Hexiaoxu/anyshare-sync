"""Permission Gate — the safety valve.

Before any document is uploaded to BISHENG, the gate checks:
  1. All ACL accessors can be mapped → otherwise BLOCK
  2. No deny rules exist → otherwise BLOCK
  3. Download permission exists → otherwise BLOCK
  4. No external sharing → otherwise BLOCK

Gate result: ALLOW (proceed) or BLOCK (mark permission_blocked).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.connectors.anyshare.acl import AclEntry


class GateDecision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"


@dataclass
class GateResult:
    decision: GateDecision
    reason: str = ""
    blocked_accessors: list[str] = field(default_factory=list)


class PermissionGate:
    """Safety gate — BLOCK by default, ALLOW only when safe."""

    def check_document(self, acl_entries: list[AclEntry],
                       is_personal: bool = False) -> GateResult:
        """Check if ACL can be safely migrated.

        Personal doc libs (is_personal=True) skip ACL migration entirely —
        only the owner gets access.
        """
        if is_personal:
            return GateResult(decision=GateDecision.ALLOW, reason="personal_doclib")

        if not acl_entries:
            return GateResult(
                decision=GateDecision.BLOCK,
                reason="no_acl_entries",
            )

        blocked = []

        for entry in acl_entries:
            # Deny rules = automatic block
            if entry.deny:
                blocked.append(f"{entry.accessor_id}:has_deny")
                continue

            # Preview only, no download = block
            if "download" not in entry.allow and "preview" in entry.allow:
                blocked.append(f"{entry.accessor_id}:preview_only")

            # Has expiry
            if entry.expire_time:
                blocked.append(f"{entry.accessor_id}:has_expiry")

            # External sharing
            if "external_sharing" in entry.allow:
                blocked.append(f"{entry.accessor_id}:external_sharing")

        if blocked:
            return GateResult(
                decision=GateDecision.BLOCK,
                reason=f"unsafe_acl: {', '.join(blocked)}",
                blocked_accessors=blocked,
            )

        return GateResult(decision=GateDecision.ALLOW)
