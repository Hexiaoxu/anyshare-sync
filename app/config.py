"""
统一配置加载模块 — 从 config/config.yaml 读取所有配置
用法: from app.config import cfg
"""
import os
from pathlib import Path
from functools import lru_cache
from dataclasses import dataclass, field
import yaml

from app import ssl_bypass  # noqa: F401 — side effect: disables httpx TLS verification

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"


@dataclass
class AnyShareConfig:
    base_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    admin_account: str = ""
    knowledge_account: str = ""
    console_user_id: str = ""
    timeout: float = 30.0


@dataclass
class BishengConfig:
    base_url: str = ""
    cookie_value: str = ""
    jwt_secret: str = ""
    jwt_issuer: str = "bisheng"
    jwt_expire_seconds: int = 86400
    jwt_admin_user_id: int = 1
    jwt_admin_user_name: str = "admin"
    jwt_admin_tenant_id: int = 1
    jwt_admin_token_version: int = 1
    timeout: float = 30.0


@dataclass
class SyncConfig:
    max_depth: int = 20
    max_objects: int = 500_000
    scan_timeout_minutes: int = 60
    retry_max: int = 6
    retry_backoff_seconds: int = 10
    missing_threshold: int = 2
    archive_days: int = 30
    temp_dir: str = "/tmp/anyshare-sync"


@dataclass
class SchedulerConfig:
    retry_due_seconds: int = 60
    poll_ingestion_seconds: int = 45
    daily_scan_time: str = "02:30"
    daily_housekeeping_time: str = "02:00"


@dataclass
class AppConfig:
    """Structured configuration used by the scan and organization services."""

    anyshare: AnyShareConfig = field(default_factory=AnyShareConfig)
    bisheng: BishengConfig = field(default_factory=BishengConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    org_excel_path: str = ""

    @classmethod
    def from_file(cls, config_path: str | Path = None) -> "AppConfig":
        path = Path(config_path or os.environ.get("SYNC_CONFIG", _CONFIG_PATH))
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        anyshare = raw.get("anyshare", {})
        bisheng = raw.get("bisheng", {})
        sync = raw.get("sync", {})
        scheduler = raw.get("scheduler", {})

        return cls(
            anyshare=AnyShareConfig(
                base_url=os.environ.get("ANYSHARE_URL", anyshare.get("base_url", "")),
                client_id=os.environ.get("ANYSHARE_CLIENT_ID", anyshare.get("client_id", "")),
                client_secret=os.environ.get("ANYSHARE_CLIENT_SECRET", anyshare.get("client_secret", "")),
                admin_account=anyshare.get("admin_account", ""),
                knowledge_account=anyshare.get("knowledge_account", anyshare.get("admin_account", "")),
                console_user_id=anyshare.get("console_user_id", ""),
                timeout=anyshare.get("timeout", 30),
            ),
            bisheng=BishengConfig(
                base_url=os.environ.get("BISHENG_URL", bisheng.get("base_url", "")),
                cookie_value=os.environ.get("BISHENG_COOKIE", bisheng.get("cookie_value", "")),
                jwt_secret=bisheng.get("jwt_secret", ""),
                jwt_issuer=bisheng.get("jwt_issuer", "bisheng"),
                jwt_expire_seconds=bisheng.get("jwt_expire_seconds", 86400),
                jwt_admin_user_id=bisheng.get("jwt_admin_user_id", 1),
                jwt_admin_user_name=bisheng.get("jwt_admin_user_name", "admin"),
                jwt_admin_tenant_id=bisheng.get("jwt_admin_tenant_id", 1),
                jwt_admin_token_version=bisheng.get("jwt_admin_token_version", 1),
                timeout=bisheng.get("timeout", 30),
            ),
            sync=SyncConfig(
                max_depth=sync.get("max_depth", 20),
                max_objects=sync.get("max_objects", sync.get("max_objects_per_scan", 500_000)),
                scan_timeout_minutes=sync.get("scan_timeout_minutes", 60),
                retry_max=sync.get("retry_max", 6),
                retry_backoff_seconds=sync.get("retry_backoff_seconds", 10),
                missing_threshold=sync.get("missing_threshold", 2),
                archive_days=sync.get("archive_days", 30),
                temp_dir=sync.get("temp_dir", "/tmp/anyshare-sync"),
            ),
            scheduler=SchedulerConfig(
                retry_due_seconds=scheduler.get("retry_due_seconds", scheduler.get("retry_due_tasks_seconds", 60)),
                poll_ingestion_seconds=scheduler.get("poll_ingestion_seconds", 45),
                daily_scan_time=scheduler.get("daily_scan_time", "02:30"),
                daily_housekeeping_time=scheduler.get("daily_housekeeping_time", "02:00"),
            ),
            org_excel_path=raw.get("org_excel_path", ""),
        )


@lru_cache(maxsize=1)
def _load() -> dict:
    path = Path(os.environ.get("SYNC_CONFIG", _CONFIG_PATH))
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class _Cfg:
    """Dot-access wrapper around config.yaml."""

    @property
    def as_base(self) -> str:
        return _load()["anyshare"]["base_url"].rstrip("/")

    @property
    def as_client_id(self) -> str:
        return _load()["anyshare"]["client_id"]

    @property
    def as_client_secret(self) -> str:
        return _load()["anyshare"]["client_secret"]

    @property
    def as_admin_account(self) -> str:
        return _load()["anyshare"]["admin_account"]

    @property
    def as_knowledge_account(self) -> str:
        return _load()["anyshare"].get("knowledge_account") or _load()["anyshare"]["admin_account"]

    @property
    def as_console_user_id(self) -> str:
        return _load()["anyshare"].get("console_user_id", "")

    @property
    def as_timeout(self) -> int:
        return _load()["anyshare"].get("timeout", 30)

    @property
    def bs_base(self) -> str:
        return _load()["bisheng"]["base_url"].rstrip("/")

    @property
    def bs_jwt_secret(self) -> str:
        return _load()["bisheng"]["jwt_secret"]

    @property
    def bs_jwt_issuer(self) -> str:
        return _load()["bisheng"].get("jwt_issuer", "bisheng")

    @property
    def bs_jwt_expire_seconds(self) -> int:
        return _load()["bisheng"].get("jwt_expire_seconds", 86400)

    @property
    def bs_admin_user_id(self) -> int:
        return _load()["bisheng"].get("jwt_admin_user_id", 1)

    @property
    def bs_admin_user_name(self) -> str:
        return _load()["bisheng"].get("jwt_admin_user_name", "admin")

    @property
    def bs_admin_tenant_id(self) -> int:
        return _load()["bisheng"].get("jwt_admin_tenant_id", 1)

    @property
    def bs_timeout(self) -> int:
        return _load()["bisheng"].get("timeout", 30)

    @property
    def db(self) -> dict:
        return _load()["database"]

    @property
    def sync(self) -> dict:
        return _load()["sync"]

    @property
    def dept_lib_mode(self) -> str:
        """部门文档库迁移模式: single 或 per_dept"""
        return _load()["sync"].get("dept_lib_mode", "single")

    @property
    def trees(self) -> list:
        return _load()["sync"].get("trees", [])

    @property
    def org_excel_path(self) -> str:
        return _load().get("org_excel_path", "")

    @property
    def log_level(self) -> str:
        return _load().get("logging", {}).get("level", "INFO")

    @property
    def scheduler_interval(self) -> int:
        """增量同步间隔（秒），默认3600"""
        return _load().get("scheduler", {}).get("interval_seconds", 3600)


cfg = _Cfg()
