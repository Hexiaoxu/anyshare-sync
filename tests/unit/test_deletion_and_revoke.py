"""AnyShare deletions must propagate to BISHENG, and revoked ACL entries
must not linger as stale grants."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.sync_pipeline import SyncPipeline


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Serves .exec() results from a queue, in call order. Ignores add/commit."""

    def __init__(self, results):
        self._results = iter(results)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def exec(self, statement):
        return _Result(next(self._results))

    def add(self, obj):
        pass

    def commit(self):
        pass


def _pipeline():
    p = SyncPipeline.__new__(SyncPipeline)
    p._init_state()
    p._space_id = 999
    p._bs_file = MagicMock()
    p._bs_folder = MagicMock()
    p._bs_perm = MagicMock()
    return p


# ── _diff_revokes ────────────────────────────────────────────

def test_diff_revokes_finds_removed_grants():
    old = [{"subject_type": "user", "subject_id": 1, "relation": "viewer"},
           {"subject_type": "user", "subject_id": 2, "relation": "viewer"}]
    new = [{"subject_type": "user", "subject_id": 1, "relation": "viewer"}]
    revokes = SyncPipeline._diff_revokes(old, new)
    assert revokes == [{"subject_type": "user", "subject_id": 2, "relation": "viewer"}]


def test_diff_revokes_empty_when_unchanged():
    grants = [{"subject_type": "user", "subject_id": 1, "relation": "viewer"}]
    assert SyncPipeline._diff_revokes(grants, grants) == []


# ── authorize_with_revoke ────────────────────────────────────

def test_authorize_with_revoke_propagates_full_revocation():
    """Access fully removed in AnyShare (grants=[]) must still revoke in BISHENG,
    not silently no-op like the old `if grants and ...` guard did."""
    p = _pipeline()
    p._bs_perm.authorize.return_value = True
    with patch.object(p, "_load_previous_grants", return_value=[
            {"subject_type": "user", "subject_id": 5, "relation": "viewer"}]), \
         patch.object(p, "_save_perm_snapshot") as save:
        ok = p.authorize_with_revoke("knowledge_file", 42, "f.docx", "gns://X", [])

    assert ok is True
    p._bs_perm.authorize.assert_called_once_with(
        "knowledge_file", 42, grants=[],
        revokes=[{"subject_type": "user", "subject_id": 5, "relation": "viewer"}],
        timeout=60, retries=2)
    save.assert_called_once()


def test_authorize_with_revoke_noop_when_nothing_granted_or_revoked():
    p = _pipeline()
    with patch.object(p, "_load_previous_grants", return_value=[]):
        ok = p.authorize_with_revoke("knowledge_file", 42, "f.docx", "gns://X", [])
    assert ok is True
    p._bs_perm.authorize.assert_not_called()


# ── _detect_and_delete_missing ───────────────────────────────

def test_missing_file_deleted_immediately_when_scan_complete():
    """_scan() either walks the whole tree or raises — so a complete scan
    (scan_truncated=False, the default) finding an object missing is
    trustworthy on the first try; no need to wait for a second run."""
    p = _pipeline()
    p._file_map = {"gns://LIB/F1": 101}
    p._folder_map = {}
    row = SimpleNamespace(source_doc_id="gns://LIB/F1", status="succeeded",
                          missing_count=0, source_name="f1.docx")
    with patch("app.sync_pipeline.init_db"), \
         patch("app.sync_pipeline.get_session", return_value=_Session([[row]])):
        result = p._detect_and_delete_missing("gns://LIB", [], [])

    assert result == {"deleted": 1, "flagged": 0}
    assert row.missing_count == 1
    assert row.status == "deleted"
    p._bs_file.delete_file.assert_called_once_with(999, 101)
    assert "gns://LIB/F1" not in p._file_map


def test_missing_file_deleted_only_after_threshold_when_scan_truncated():
    """When the scan itself hit max_depth/dir/file caps (scan_truncated=True),
    "missing" could just mean "the scan didn't reach it" — fall back to
    requiring sync.missing_threshold consecutive misses before deleting."""
    p = _pipeline()
    p._file_map = {"gns://LIB/F1": 101}
    p._folder_map = {}

    # Run 1: missing_count 0 -> 1, still below threshold=2 -> not deleted.
    row = SimpleNamespace(source_doc_id="gns://LIB/F1", status="succeeded",
                          missing_count=0, source_name="f1.docx")
    with patch("app.sync_pipeline.init_db"), \
         patch("app.sync_pipeline.get_session", return_value=_Session([[row]])), \
         patch("app.config.cfg") as cfg:
        cfg.sync = {"missing_threshold": 2}
        result = p._detect_and_delete_missing("gns://LIB", [], [], scan_truncated=True)

    assert result == {"deleted": 0, "flagged": 1}
    assert row.missing_count == 1
    assert row.status == "succeeded"
    p._bs_file.delete_file.assert_not_called()
    assert p._file_map == {"gns://LIB/F1": 101}  # untouched

    # Run 2: same row, now missing_count 1 -> 2, hits threshold -> deleted.
    with patch("app.sync_pipeline.init_db"), \
         patch("app.sync_pipeline.get_session", return_value=_Session([[row]])), \
         patch("app.config.cfg") as cfg:
        cfg.sync = {"missing_threshold": 2}
        result = p._detect_and_delete_missing("gns://LIB", [], [], scan_truncated=True)

    assert result == {"deleted": 1, "flagged": 0}
    assert row.missing_count == 2
    assert row.status == "deleted"
    p._bs_file.delete_file.assert_called_once_with(999, 101)
    assert "gns://LIB/F1" not in p._file_map


def test_present_file_is_not_flagged_missing():
    p = _pipeline()
    p._file_map = {"gns://LIB/F1": 101}
    p._folder_map = {}
    with patch("app.sync_pipeline.init_db"), \
         patch("app.sync_pipeline.get_session", return_value=_Session([])):
        result = p._detect_and_delete_missing(
            "gns://LIB", [], [{"id": "gns://LIB/F1", "name": "f1.docx"}])
    assert result == {"deleted": 0, "flagged": 0}
    p._bs_file.delete_file.assert_not_called()


def test_out_of_scope_mapping_is_ignored():
    """A mapping from a different doc lib must never be treated as missing
    just because this scan's lib_gns doesn't cover it."""
    p = _pipeline()
    p._file_map = {"gns://OTHER_LIB/F1": 101}
    p._folder_map = {}
    with patch("app.sync_pipeline.init_db"), \
         patch("app.sync_pipeline.get_session", return_value=_Session([])):
        result = p._detect_and_delete_missing("gns://LIB", [], [])
    assert result == {"deleted": 0, "flagged": 0}
