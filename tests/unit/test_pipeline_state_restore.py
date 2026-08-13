"""Persistent mappings are restored for daemon restarts."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.sync_pipeline import SyncPipeline


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, results):
        self._results = iter(results)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def exec(self, statement):
        return _Result(next(self._results))


def test_restore_state_populates_runtime_maps():
    pipeline = SyncPipeline.__new__(SyncPipeline)
    pipeline._init_state()
    session = _Session([
        [SimpleNamespace(target_space_id=7)],
        [SimpleNamespace(
            source_folder_id="gns://LIB/FOLDER", target_folder_id=11,
            status="active", source_name="目录", source_path="目录")],
        [SimpleNamespace(
            source_doc_id="gns://LIB/FOLDER/FILE", target_file_id=22,
            status="succeeded", source_name="文件.docx")],
    ])

    with patch("app.sync_pipeline.init_db"), \
         patch("app.sync_pipeline.get_session", return_value=session):
        result = pipeline.restore_state()

    assert result == {"spaces": 1, "folders": 1, "files": 1}
    assert pipeline._folder_map["gns://LIB/FOLDER"] == 11
    assert pipeline._file_map["gns://LIB/FOLDER/FILE"] == 22
    assert pipeline.resolve_uuid("FILE") == "gns://LIB/FOLDER/FILE"
    assert pipeline._bs_folder_by_path["目录"] == 11
