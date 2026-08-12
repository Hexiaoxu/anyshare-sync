"""Generate BISHENG JWT tokens from config.yaml — no browser needed."""
import json, time, base64, hmac, hashlib
from pathlib import Path
import yaml

CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "config.yaml"

def _load_bs_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f).get("bisheng", {})


def generate_bs_token(user_id: int = None, user_name: str = None,
                      tenant_id: int = None, token_version: int = None) -> str:
    """Generate a BISHENG access_token_cookie JWT.

    Reads defaults from config.yaml bisheng section.
    """
    cfg = _load_bs_config()
    if user_id is None:
        user_id = cfg.get("jwt_admin_user_id", 1)
    if user_name is None:
        user_name = cfg.get("jwt_admin_user_name", "admin")
    if tenant_id is None:
        tenant_id = cfg.get("jwt_admin_tenant_id", 1)
    if token_version is None:
        token_version = cfg.get("jwt_admin_token_version", 1)

    secret = cfg.get("jwt_secret", "")
    expire = int(time.time()) + cfg.get("jwt_expire_seconds", 86400)
    issuer = cfg.get("jwt_issuer", "bisheng")

    def b64url(d):
        if isinstance(d, str): d = d.encode()
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    sub = json.dumps({"user_id": user_id, "user_name": user_name,
                      "tenant_id": tenant_id, "token_version": token_version})
    h = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")))
    p = b64url(json.dumps({"sub": sub, "exp": expire, "iss": issuer}, separators=(",", ":")))
    sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{b64url(sig)}"


if __name__ == "__main__":
    print(generate_bs_token())
