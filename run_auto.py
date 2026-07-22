"""One-click auto-sync daemon.

First run: prompts for tokens, encrypts and stores them.
Subsequent runs: loads stored tokens, App Token auto-refreshes.
Browser/Console tokens expire ~1h — script warns and prompts re-entry.

Usage:
    python run_auto.py                    # interactive token entry
    python run_auto.py --headless         # use stored tokens, fail if expired
"""

import os, sys, json, base64, getpass
from pathlib import Path
from cryptography.fernet import Fernet

TOKEN_FILE = Path(__file__).parent / "data" / "tokens.enc"
KEY_FILE = Path(__file__).parent / "data" / ".key"

# ── Token storage ─────────────────────────────────────────

def _get_key():
    """Get or create encryption key."""
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(key)
    return key

def _encrypt(data: dict) -> bytes:
    f = Fernet(_get_key())
    return f.encrypt(json.dumps(data).encode())

def _decrypt(cipher: bytes) -> dict:
    f = Fernet(_get_key())
    return json.loads(f.decrypt(cipher))

def save_tokens(as_browser: str, bs_cookie: str, console: str = ""):
    """Encrypt and store tokens."""
    data = {"as_browser": as_browser, "bs_cookie": bs_cookie,
            "console": console}
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_bytes(_encrypt(data))
    print("Tokens saved to", TOKEN_FILE)

def load_tokens() -> dict | None:
    """Load stored tokens. Returns None if file missing or corrupt."""
    if not TOKEN_FILE.exists():
        return None
    try:
        return _decrypt(TOKEN_FILE.read_bytes())
    except Exception:
        print("Token file corrupted, please re-enter tokens")
        return None

# ── Interactive input ─────────────────────────────────────

def prompt_tokens() -> dict:
    """Prompt user for all tokens."""
    print("\n=== Token Setup ===")
    print("\n1. AnyShare Browser Token (F12 → AnyShare page):")
    print("   document.cookie.match(/client\\.oauth2_token=([^;]+)/)[1]")
    as_browser = input("   Browser Token: ").strip()

    print("\n2. BISHENG Cookie (F12 → BISHENG page):")
    print("   document.cookie.match(/access_token_cookie=([^;]+)/)[1]")
    bs_cookie = input("   BISHENG Cookie: ").strip()

    print("\n3. AnyShare Console Token (F12 → Console /console/):")
    print("   document.cookie.match(/console\\.oauth2_token=([^;]+)/)[1]")
    console = input("   Console Token (optional): ").strip()

    return {"as_browser": as_browser, "bs_cookie": bs_cookie, "console": console}

# ── Main ──────────────────────────────────────────────────

HEADLESS = "--headless" in sys.argv

# Load or prompt tokens
tokens = load_tokens()
if tokens is None or not HEADLESS:
    tokens = prompt_tokens()
    save_tokens(tokens["as_browser"], tokens["bs_cookie"], tokens.get("console", ""))

AS_TOKEN = tokens["as_browser"]
BS_COOKIE = tokens["bs_cookie"]
CT_TOKEN = tokens.get("console", "")

# App token can auto-refresh — get it via auth.py
from app.connectors.anyshare.auth import AnyShareAuth
try:
    auth = AnyShareAuth(
        "https://5j-zsgl.powerchina.cn",
        "7b98e7b6-f35e-4613-aeed-5b13112b0ff8",
        "Test123.",
    )
    app_token = auth.get_app_token()
    print(f"App Token auto-refreshed: {app_token[:40]}...")
except Exception as e:
    print(f"App Token failed: {e}")
    app_token = AS_TOKEN  # fallback

# What to run?
print("\n=== Select Mode ===")
print("1. Full sync (知识库)")
print("2. Full sync (部门库, skip download)")
print("3. Personal lib (--user)")
print("4. Batch all scopes from config")
print("5. Daemon (hourly incremental)")
print("6. List libraries")

mode = input("\nChoice [1-6]: ").strip()

if mode == "1":
    gns = input("GNS: ").strip() or "gns://1A71734693F8464A9B8C1980D4AFBB44"
    name = input("Space name: ").strip() or "公司资质_auto"
    cmd = f'python run_sync.py "{AS_TOKEN}" "{BS_COOKIE}" "{gns}" "{name}"'

elif mode == "2":
    gns = input("GNS: ").strip()
    name = input("Space name: ").strip()
    ancestors = input("Ancestors (comma-separated): ").strip()
    ancestor_arg = f' --ancestors "{ancestors}"' if ancestors else ""
    cmd = f'python run_sync.py "{AS_TOKEN}" "{BS_COOKIE}" "{gns}" "{name}" --type department_doc_lib --skip-download{ancestor_arg}'

elif mode == "3":
    user = input("Username: ").strip()
    cmd = f'python run_sync.py "{BS_COOKIE}" --user {user}'

elif mode == "4":
    cmd = f'python run_sync.py "{AS_TOKEN}" "{BS_COOKIE}" --batch'

elif mode == "5":
    gns = input("GNS: ").strip() or "gns://1A71734693F8464A9B8C1980D4AFBB44"
    name = input("Space name: ").strip() or "公司资质_auto"
    interval = input("Interval seconds (default 3600): ").strip() or "3600"
    if not CT_TOKEN:
        CT_TOKEN = input("Console Token required for daemon: ").strip()
        tokens["console"] = CT_TOKEN
        save_tokens(tokens["as_browser"], tokens["bs_cookie"], CT_TOKEN)
    cmd = f'python run_sync.py "{AS_TOKEN}" "{BS_COOKIE}" "{gns}" "{name}" --daemon "{CT_TOKEN}" --interval {interval}'

elif mode == "6":
    list_type = input("Type (knowledge/department/personal): ").strip() or "knowledge"
    cmd = f'python run_sync.py "{AS_TOKEN}" "{BS_COOKIE}" --list {list_type}'

else:
    print("Invalid choice")
    sys.exit(1)

print(f"\nRunning: {cmd[:120]}...")
os.system(cmd)
