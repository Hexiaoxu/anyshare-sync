"""Log-driven incremental sync — auto-run every N seconds.

Pulls Console EACPLog for changes since the last run,
dispatches to LogEventHandler, persists checkpoint timestamp.
"""

import json
import time
import logging
from datetime import datetime
from pathlib import Path

import httpx as hx

from app.sync_pipeline import SyncPipeline
from app.services.log_event_handler import LogEventHandler
from app.config import cfg as _global_cfg

logger = logging.getLogger("log_scheduler")

CHECKPOINT_FILE = Path(__file__).parent.parent.parent / "data" / "log_sync_checkpoint.json"
DEFAULT_INTERVAL = 3600  # 1 hour
USER_ID = _global_cfg.as_console_user_id


class LogSyncScheduler:
    """Periodic incremental sync via Console EACPLog."""

    def __init__(self, pipeline: SyncPipeline, console_token: str,
                 bs_cookie: str, interval: int = DEFAULT_INTERVAL):
        self._pipeline = pipeline
        self._ct = console_token
        self._bs_cookie = bs_cookie
        self._interval = interval
        self._handler = LogEventHandler(pipeline, bs_cookie)
        self._stop = False

    # ── Checkpoint ─────────────────────────────────────────

    def _load_checkpoint(self) -> int:
        """Load last sync timestamp (microseconds). Returns 0 if never run."""
        if CHECKPOINT_FILE.exists():
            try:
                data = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
                ts = data.get("last_sync_us", 0)
                logger.info(f"Checkpoint loaded: {ts} ({self._fmt_ts(ts)})")
                return ts
            except Exception:
                pass
        return 0

    def _save_checkpoint(self, ts_us: int):
        """Save last sync timestamp."""
        CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "last_sync_us": ts_us,
            "last_sync_time": self._fmt_ts(ts_us),
            "updated_at": datetime.now().isoformat(),
        }
        CHECKPOINT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
        logger.info(f"Checkpoint saved: {ts_us}")

    @staticmethod
    def _fmt_ts(ts_us: int) -> str:
        if ts_us == 0:
            return "never"
        return datetime.fromtimestamp(ts_us / 1_000_000).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _now_us() -> int:
        """Current time in microseconds."""
        return int(time.time() * 1_000_000)

    # ── Log pull ───────────────────────────────────────────

    def _pull_logs(self, since_us: int, until_us: int) -> tuple[list[dict], bool]:
        """Pull all logs. The bool is False if any page/type was incomplete."""
        events = []
        complete = True
        from app.config import cfg as _cfg
        AS_BASE = _cfg.as_base

        # 刷新 token
        try:
            from app.connectors.anyshare.auth import AnyShareAuth
            auth = AnyShareAuth(AS_BASE, _cfg.as_client_id, _cfg.as_client_secret)
            self._ct = auth.get_user_token(_cfg.as_admin_account)
            logger.info("AnyShare token refreshed")
        except Exception as e:
            logger.warning(f"Token refresh failed, using existing: {e}")

        for logType in [11, 12]:  # 11=组织, 12=文档 (10=登录 忽略)
            start = 0
            while True:
                body = [{'ncTGetPageLogParam': {
                    'userId': USER_ID,
                    'start': start, 'limit': 500,
                    'maxLogId': 9223372036854775807,
                    'logType': logType,
                    'levels': [], 'macs': [], 'ips': [], 'displayNames': [],
                    'opTypes': [],  # 空=所有类型
                    'msgs': [], 'exMsgs': [],
                    'startDate': since_us,
                    'endDate': until_us,
                }}]
                try:
                    r = hx.post(
                        f'{AS_BASE}/console/api/EACPLog/GetPageLog',
                        json=body, timeout=30,
                        headers={'Authorization': f'Bearer {self._ct}',
                                 'Content-Type': 'application/json;charset=UTF-8'})
                    if r.status_code != 200:
                        logger.warning(f"Log pull HTTP {r.status_code}: {r.text[:100]}")
                        complete = False
                        break
                    data = r.json()
                    if not data:
                        break
                    events.extend(data)
                    start += len(data)
                    if len(data) < 500:
                        break
                except Exception as e:
                    logger.error(f"Log pull error: {e}")
                    complete = False
                    break
        return events, complete

    # ── Main loop ──────────────────────────────────────────

    def run_once(self) -> dict:
        """Single incremental sync cycle."""
        since_us = self._load_checkpoint()
        if since_us == 0:
            # First run: set checkpoint to now, skip (need full sync first)
            since_us = self._now_us()
            self._save_checkpoint(since_us)
            logger.info("First run — checkpoint initialized. Run full sync first.")
            return {"status": "init", "events": 0, "synced": 0}

        until_us = self._now_us()
        logger.info(f"Pulling logs: {self._fmt_ts(since_us)} → {self._fmt_ts(until_us)}")

        events, complete = self._pull_logs(since_us, until_us)
        if not complete:
            logger.error("Log pull incomplete; checkpoint retained at %s",
                         self._fmt_ts(since_us))
            return {"status": "retry", "events": len(events),
                    "errors": 1, "reason": "log_pull_incomplete"}
        if not events:
            logger.info("No new events")
            self._save_checkpoint(until_us)
            return {"status": "ok", "events": 0, "synced": 0}

        logger.info(f"Processing {len(events)} events...")
        result = self._handler.handle(events)

        if result.get("errors", 0):
            logger.error("Cycle has %s handler error(s); checkpoint retained at %s",
                         result["errors"], self._fmt_ts(since_us))
            return {"status": "retry", "events": len(events), **result}

        self._save_checkpoint(until_us)
        logger.info(f"Cycle done: {result}")
        return {"status": "ok", "events": len(events), **result}

    def run_forever(self):
        """Run sync loop indefinitely."""
        logger.info(f"Log sync scheduler started (interval={self._interval}s)")
        while not self._stop:
            try:
                self.run_once()
            except Exception:
                logger.exception("Sync cycle failed — will retry")
            for _ in range(self._interval):
                if self._stop:
                    break
                time.sleep(1)

    def stop(self):
        self._stop = True
