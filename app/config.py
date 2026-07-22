"""Configuration loader — reads config.yaml, env vars, with encrypted token support.

Secrets (client_secret, cookie_value) can be stored as:
  - Plain text in config.yaml (dev only)
  - Environment variable (recommended for production)
  - Fernet-encrypted value in config.yaml (prefix: "enc:")

Encryption helper:
  python -c "from app.config import encrypt; print(encrypt('my-secret'))"
"""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field

import yaml

# Fernet key — in production, set via env var BS_ENCRYPTION_KEY
_ENCRYPTION_KEY = os.getenv(
    "BS_ENCRYPTION_KEY",
    "TI31VYJ-ldAq-FXo5QNPKV_lqGTFfp-MIdbK2Hm5F1E=",
)


def _get_fernet():
    from cryptography.fernet import Fernet
    # Ensure key is valid base64
    key = _ENCRYPTION_KEY
    if len(key) < 44:
        import base64
        key = base64.urlsafe_b64encode(key.ljust(32)[:32].encode()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    """Encrypt a secret value for config.yaml storage."""
    f = _get_fernet()
    return "enc:" + f.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Decrypt a config value (plain or 'enc:'-prefixed)."""
    if not value:
        return value
    if value.startswith("enc:"):
        f = _get_fernet()
        return f.decrypt(value[4:].encode()).decode()
    return value


@dataclass
class AnyShareConfig:
    base_url: str = "https://your-anyshare.example.com"
    client_id: str = ""
    client_secret: str = ""   # supports "enc:..." prefix
    timeout: float = 30.0

    def get_secret(self) -> str:
        return decrypt(self.client_secret)


@dataclass
class BishengConfig:
    base_url: str = "http://your-bisheng.example.com:7860"
    cookie_value: str = ""    # supports "enc:..." prefix
    timeout: float = 30.0

    def get_cookie(self) -> str:
        return decrypt(self.cookie_value)


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
    anyshare: AnyShareConfig = field(default_factory=AnyShareConfig)
    bisheng: BishengConfig = field(default_factory=BishengConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)


def load_config(config_path: str | Path = None) -> AppConfig:
    """Load configuration from YAML, env vars take precedence."""
    if config_path is None:
        config_path = Path(__file__).parent.parent / "config" / "config.yaml"

    raw = {}
    if Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    anyshare_raw = raw.get("anyshare", {})
    bisheng_raw = raw.get("bisheng", {})

    return AppConfig(
        anyshare=AnyShareConfig(
            base_url=os.getenv("ANYSHARE_URL", anyshare_raw.get("base_url", "")),
            client_id=os.getenv("ANYSHARE_CLIENT_ID", anyshare_raw.get("client_id", "")),
            client_secret=os.getenv("ANYSHARE_CLIENT_SECRET", anyshare_raw.get("client_secret", "")),
        ),
        bisheng=BishengConfig(
            base_url=os.getenv("BISHENG_URL", bisheng_raw.get("base_url", "")),
            cookie_value=os.getenv("BISHENG_COOKIE", bisheng_raw.get("cookie_value", "")),
        ),
        sync=SyncConfig(**raw.get("sync", {})),
        scheduler=SchedulerConfig(**raw.get("scheduler", {})),
    )
