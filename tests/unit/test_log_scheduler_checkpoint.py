"""Checkpoint must not advance when an incremental event fails."""

from unittest.mock import MagicMock, patch

from app.services.log_scheduler import LogSyncScheduler


def _scheduler() -> LogSyncScheduler:
    pipeline = MagicMock()
    pipeline._bs._url = "http://bisheng.test"
    return LogSyncScheduler(pipeline, "as-token", "bs-token", interval=1)


def test_handler_error_retains_checkpoint():
    scheduler = _scheduler()
    scheduler._handler.handle = MagicMock(return_value={"errors": 1, "stats": {}})

    with patch.object(scheduler, "_load_checkpoint", return_value=100), \
         patch.object(scheduler, "_now_us", return_value=200), \
         patch.object(scheduler, "_pull_logs", return_value=([{"opType": 2}], True)), \
         patch.object(scheduler, "_save_checkpoint") as save:
        result = scheduler.run_once()

    assert result["status"] == "retry"
    save.assert_not_called()


def test_success_advances_checkpoint():
    scheduler = _scheduler()
    scheduler._handler.handle = MagicMock(return_value={"errors": 0, "stats": {}})

    with patch.object(scheduler, "_load_checkpoint", return_value=100), \
         patch.object(scheduler, "_now_us", return_value=200), \
         patch.object(scheduler, "_pull_logs", return_value=([{"opType": 2}], True)), \
         patch.object(scheduler, "_save_checkpoint") as save:
        result = scheduler.run_once()

    assert result["status"] == "ok"
    save.assert_called_once_with(200)


def test_incomplete_log_pull_retains_checkpoint():
    scheduler = _scheduler()

    with patch.object(scheduler, "_load_checkpoint", return_value=100), \
         patch.object(scheduler, "_now_us", return_value=200), \
         patch.object(scheduler, "_pull_logs", return_value=([], False)), \
         patch.object(scheduler, "_save_checkpoint") as save:
        result = scheduler.run_once()

    assert result["status"] == "retry"
    assert result["reason"] == "log_pull_incomplete"
    save.assert_not_called()
