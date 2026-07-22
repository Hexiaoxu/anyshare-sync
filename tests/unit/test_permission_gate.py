"""Test permission gate decisions."""

from app.connectors.anyshare.acl import AclEntry
from app.services.permission_gate import PermissionGate, GateDecision


class TestPermissionGate:
    def setup_method(self):
        self.gate = PermissionGate()

    def test_personal_doclib_always_allowed(self):
        assert self.gate.check_document([], is_personal=True).decision == GateDecision.ALLOW

    def test_deny_blocks(self):
        entries = [AclEntry(accessor_id="u1", accessor_type="user",
                            allow=["display", "preview", "download"],
                            deny=["download"])]
        result = self.gate.check_document(entries)
        assert result.decision == GateDecision.BLOCK
        assert "deny" in result.reason.lower()

    def test_preview_only_blocks(self):
        entries = [AclEntry(accessor_id="u1", accessor_type="user",
                            allow=["display", "preview"])]
        result = self.gate.check_document(entries)
        assert result.decision == GateDecision.BLOCK

    def test_clean_acl_allows(self):
        entries = [AclEntry(accessor_id="u1", accessor_type="user",
                            allow=["display", "preview", "download"])]
        result = self.gate.check_document(entries)
        assert result.decision == GateDecision.ALLOW

    def test_no_acl_blocks_public(self):
        """Public doc libs with empty ACL should block (can't verify safety)."""
        result = self.gate.check_document([], is_personal=False)
        assert result.decision == GateDecision.BLOCK
