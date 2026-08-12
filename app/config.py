"""
统一配置加载模块 — 从 config/config.yaml 读取所有配置
用法: from app.config import cfg
"""
import os
from pathlib import Path
from functools import lru_cache
import yaml

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"


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
