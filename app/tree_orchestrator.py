"""Tree orchestrator — one space, many sub-libraries as folder tree."""

import logging
import time
import yaml
from pathlib import Path

from app.sync_pipeline import SyncPipeline

logger = logging.getLogger("tree_orch")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"


class TreeOrchestrator:
    """Runs tree-structured sync: one BISHENG space with sub-folders.

    Config format (in config.yaml):
        sync:
          trees:
            - space_name: "知识库"
              type: "knowledge_doc_lib"
              no_root_perms: true
              skip_download: false
              items:
                - name: "管理办法"
                  gns: "gns://..."
                - name: "公司资质"
                  gns: "gns://..."
    """

    def __init__(self, bs_base: str, bs_cookie: str,
                 as_base: str, as_token: str, as_auth=None):
        self._pipeline = SyncPipeline(
            bs_base=bs_base, bs_cookie=bs_cookie,
            as_base=as_base, as_token=as_token,
            as_auth=as_auth,
        )

    def run_all(self) -> list[dict]:
        """Run all tree configs."""
        config = self._load_config()
        trees = config.get("sync", {}).get("trees", [])
        if not trees:
            logger.warning("No tree configs found in config.yaml")
            return []

        results = []
        total_start = time.time()

        for tree in trees:
            space_name = tree["space_name"]
            source_type = tree.get("type", "knowledge_doc_lib")
            no_root = tree.get("no_root_perms", True)
            skip_dl = tree.get("skip_download", False)
            items = tree.get("items", [])

            logger.info(f"=== Tree: {space_name} ({len(items)} items) ===")

            for i, item in enumerate(items):
                name = item["name"]
                gns = item["gns"]
                ancestors = item.get("ancestors")
                incremental = (i > 0)  # first creates space, rest reuse

                logger.info(f"[{i+1}/{len(items)}] {space_name}/{name} "
                            f"{'incr' if incremental else 'new'}")

                try:
                    result = self._pipeline.run(
                        lib_gns=gns,
                        space_name=space_name,
                        ancestors=ancestors.split(",") if ancestors else [name],
                        skip_download=skip_dl,
                        source_type=source_type,
                        incremental=incremental,
                        no_root_perms=no_root,
                    )
                    result["tree"] = space_name
                    result["item"] = name
                    results.append(result)

                    logger.info(f"  OK: {result.get('dirs',0)}D/{result.get('files',0)}F, "
                                f"ACL={result.get('acl_synced','?')}, "
                                f"{result.get('elapsed_sec',0):.0f}s")

                except Exception as e:
                    logger.exception(f"  FAILED: {name}")
                    results.append({"tree": space_name, "item": name,
                                    "error": str(e)})

        elapsed = time.time() - total_start
        ok = sum(1 for r in results if not r.get("error"))
        logger.info(f"=== Trees done: {ok}/{len(results)} OK in {elapsed:.0f}s ===")
        return results

    @staticmethod
    def _load_config() -> dict:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
