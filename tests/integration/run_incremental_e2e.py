"""Run an isolated real incremental-sync smoke test.

The test uses a temporary SQLite database and a temporary BISHENG space. It
does not alter the production Dameng mappings or the Console log checkpoint.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WORK = Path(tempfile.mkdtemp(prefix="anyshare_incremental_e2e_"))
os.environ["SYNC_DB_TYPE"] = "sqlite"
os.environ["SYNC_SQLITE_PATH"] = str(WORK / "state.db")

from app.config import cfg
from app.connectors.anyshare.auth import AnyShareAuth
from app.connectors.anyshare.downloader import AnyShareDownloader
from app.models import get_session, init_db
from app.models.document_mapping import SyncDocumentMapping
from app.models.space_mapping import SyncSpaceMapping
from app.services.log_event_handler import LogEventHandler
from app.sync_pipeline import SKIP_EXTENSIONS, SyncPipeline
from sqlmodel import select


def request_with_retry(method: str, url: str, **kwargs) -> httpx.Response:
    for attempt in range(3):
        try:
            response = httpx.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.TransportError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def find_small_file(token: str) -> tuple[str, str, dict]:
    """Return (library root GNS, library name, file record)."""
    roots = []
    for tree in cfg.trees:
        for item in tree.get("items", []):
            if item.get("gns"):
                roots.append((item["gns"], item.get("name", "incremental-e2e")))
    roots.append(("gns://110F8E071F0243AEBDB4DFD59F52D131", "personal-e2e"))

    headers = {"Authorization": f"Bearer {token}"}
    for root_gns, root_name in roots:
        queue = [(root_gns, 0)]
        visited = set()
        while queue:
            gns, depth = queue.pop(0)
            if gns in visited or depth > 4:
                continue
            visited.add(gns)
            try:
                response = request_with_retry(
                    "GET",
                    f"{cfg.as_base}/api/efast/v1/folders/{quote(gns, safe='')}/sub_objects",
                    headers=headers,
                    params={"limit": 100, "sort": "name", "direction": "asc"},
                    timeout=30,
                )
            except (httpx.HTTPError, RuntimeError):
                continue
            page = response.json()
            queue.extend((entry["id"], depth + 1) for entry in page.get("dirs", []))
            for entry in page.get("files", []):
                suffix = Path(entry.get("name", "")).suffix.lower()
                size = int(entry.get("size", 0) or 0)
                if suffix not in SKIP_EXTENSIONS and 0 < size <= 2 * 1024 * 1024:
                    return root_gns, root_name, entry
    raise RuntimeError("No accessible file smaller than 2 MiB was found")


def children(pipeline: SyncPipeline, space_id: int) -> list[dict]:
    result = pipeline._bs_folder.list_children(space_id, page_size=200)
    if isinstance(result, list):
        return result
    return result.get("data", [])


def main() -> None:
    auth = AnyShareAuth(
        cfg.as_base, cfg.as_client_id, cfg.as_client_secret, cfg.as_timeout)
    pipeline = None
    space_id = None
    try:
        user_token = auth.get_user_token(cfg.as_admin_account)
        lib_gns, lib_name, source = find_small_file(user_token)
        source_gns = source["id"]
        source_uuid = source_gns.rstrip("/").rsplit("/", 1)[-1]
        source_name = source["name"]
        print(f"SOURCE name={source_name!r} size={source.get('size')} gns={source_gns}")

        pipeline = SyncPipeline(
            cfg.bs_base, "", cfg.as_base, user_token,
            as_auth=auth, as_account=cfg.as_admin_account)
        space_name = f"E2E_incremental_{uuid.uuid4().hex[:10]}"
        space_id = pipeline._bs_space.create_personal(
            space_name, "isolated incremental sync test")
        print(f"SPACE created id={space_id} name={space_name}")

        downloader = AnyShareDownloader(
            cfg.as_base,
            lambda: auth.get_user_token(cfg.as_admin_account),
            timeout=120,
        )
        info = downloader.get_download_info(source_gns, source_name)
        if not info.name:
            info.name = source_name
        local = downloader.download_to_file(info, WORK / "baseline")
        uploaded = pipeline._bs_file.upload_to_minio(space_id, local)
        baseline = pipeline._bs_file.register(space_id, uploaded)
        baseline_id = baseline["id"]

        init_db()
        with get_session() as session:
            space = SyncSpaceMapping(
                source_doc_lib_id=lib_gns,
                source_doc_lib_name=lib_name,
                source_type="incremental_e2e",
                target_space_id=space_id,
                status="active",
            )
            session.add(space)
            session.commit()
            session.refresh(space)
            session.add(SyncDocumentMapping(
                space_mapping_id=space.id,
                source_doc_id=source_gns,
                source_name=source_name,
                source_size=int(source.get("size", 0) or 0),
                target_file_id=baseline_id,
                idempotency_key=uuid.uuid4().hex,
                status="succeeded",
            ))
            session.commit()
        print(f"BASELINE registered file_id={baseline_id}")

        # Simulate a daemon restart: state must be restored solely from DB.
        restarted = SyncPipeline(
            cfg.bs_base, "", cfg.as_base, user_token,
            as_auth=auth, as_account=cfg.as_admin_account)
        restored = restarted.restore_state()
        assert restarted._file_map[source_gns] == baseline_id
        restarted._build_grants_for_gns = lambda _: []
        print(f"RESTORE {restored}")

        event = {
            "logType": 12,
            "opType": 19,
            "objId": source_uuid,
            "userId": "incremental-e2e",
            "userName": "incremental-e2e",
            "date": int(time.time() * 1_000_000),
            "msg": f'rename "{source_name}" success',
            "exMsg": "",
        }
        result = LogEventHandler(restarted).handle([event])
        assert result["errors"] == 0, result
        replacement_id = restarted._file_map[source_gns]
        assert replacement_id != baseline_id
        current = children(restarted, space_id)
        assert len(current) == 1, current
        assert current[0]["id"] == replacement_id, current
        with get_session() as session:
            mapping = session.exec(select(SyncDocumentMapping).where(
                SyncDocumentMapping.source_doc_id == source_gns)).first()
            assert mapping.target_file_id == replacement_id
        print(f"MODIFY old_id={baseline_id} new_id={replacement_id} children=1")

        # Replay after another restart must reuse the mapping, not duplicate it.
        replayed = SyncPipeline(
            cfg.bs_base, "", cfg.as_base, user_token,
            as_auth=auth, as_account=cfg.as_admin_account)
        replayed.restore_state()
        replayed._build_grants_for_gns = lambda _: []
        replay = dict(event, opType=2, msg=f'upload "{source_name}" success')
        replay_result = LogEventHandler(replayed).handle([replay])
        assert replay_result["errors"] == 0, replay_result
        after_replay = children(replayed, space_id)
        assert len(after_replay) == 1, after_replay
        assert after_replay[0]["id"] == replacement_id, after_replay
        print(f"REPLAY reused file_id={replacement_id} children=1")
        print("RESULT PASS")
    finally:
        if pipeline is not None and space_id is not None:
            response = pipeline._bs._delete(f"/api/v1/knowledge/space/{space_id}")
            pipeline._bs.ok(response)
            print(f"CLEANUP deleted space_id={space_id}")
        auth.close()
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
