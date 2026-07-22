"""Batch orchestrator — reads config.yaml scopes and runs SyncPipeline for each."""

from __future__ import annotations

import logging
import time
import yaml
from pathlib import Path

from app.sync_pipeline import SyncPipeline
from app.logger import get_logger

logger = get_logger("batch_orch")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"


class BatchOrchestrator:
    """Reads sync scopes from config and runs them sequentially."""

    def __init__(self, bs_base: str, bs_cookie: str,
                 as_base: str, as_token: str, as_auth=None):
        self._pipeline = SyncPipeline(
            bs_base=bs_base, bs_cookie=bs_cookie,
            as_base=as_base, as_token=as_token,
            as_auth=as_auth,
        )

    def run_all(self, incremental: bool = False) -> list[dict]:
        """Run all enabled scopes. Returns list of results."""
        config = self._load_config()
        scopes = config.get("sync", {}).get("scopes", [])
        enabled = [s for s in scopes if s.get("enabled", True)]

        if not enabled:
            logger.warning("No enabled sync scopes found in config")
            return []

        logger.info(f"=== Batch sync: {len(enabled)} scopes, "
                     f"{'incremental' if incremental else 'full'} mode ===")

        results = []
        total_start = time.time()

        for i, scope in enumerate(enabled):
            name = scope.get("space_name", scope["source_gns"])
            logger.info(f"[{i+1}/{len(enabled)}] {name}")

            try:
                result = self._pipeline.run(
                    lib_gns=scope["source_gns"],
                    space_name=scope["space_name"],
                    ancestors=scope.get("ancestors"),
                    skip_download=scope.get("skip_download", False),
                    source_type=scope.get("source_type", "knowledge_doc_lib"),
                    incremental=incremental,
                    grant_owner=scope.get("grant_owner"),
                )
                result["scope_index"] = i
                results.append(result)

                if result.get("error"):
                    logger.error(f"  FAILED: {result['error'][:100]}")
                else:
                    logger.info(f"  OK: {result.get('transferred',0)}/{result.get('files',0)} files, "
                                f"ACL {result.get('acl_synced','?')}, "
                                f"{result.get('elapsed_sec',0):.0f}s")

            except Exception:
                logger.exception(f"  CRASHED: {name}")
                results.append({
                    "scope_index": i, "space_name": name,
                    "error": str(e),
                })

        elapsed = time.time() - total_start
        ok = sum(1 for r in results if not r.get("error"))
        logger.info(f"=== Batch done: {ok}/{len(results)} OK in {elapsed:.0f}s ===")
        return results

    def _load_config(self) -> dict:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
