import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

def _get_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}

def _get_int(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_proxy_list(raw_value):
    if not raw_value:
        return []

    # Accept comma, semicolon, and newline separated proxy entries.
    normalized = raw_value.replace("\r", "\n").replace(";", "\n").replace(",", "\n")
    proxies = []
    for candidate in normalized.split("\n"):
        value = candidate.strip().strip('"').strip("'")
        if not value or value.startswith("#"):
            continue
        proxies.append(value)
    return proxies


def _load_proxies_from_file(file_path):
    if not file_path:
        return []

    path = Path(file_path).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path

    try:
        if not path.exists():
            print(f"Warning: PROXIES_FILE not found: {path}")
            return []
        return _parse_proxy_list(path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"Warning: failed to read PROXIES_FILE '{path}': {exc}")
        return []

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

NIE = os.getenv("NIE")
NAME = os.getenv("NAME")
NATIONALITY = os.getenv("NATIONALITY")

TARGET_PROVINCE = os.getenv("TARGET_PROVINCE", "Barcelona")
TARGET_PROCEDURE_TEXT = os.getenv("TARGET_PROCEDURE_TEXT", "TOMA DE HUELLAS")

HEADLESS = _get_bool("HEADLESS", True)
PAGE_TIMEOUT_MS = _get_int("PAGE_TIMEOUT_MS", 45000)

ACTION_DELAY_MIN_MS = _get_int("ACTION_DELAY_MIN_MS", 300)
ACTION_DELAY_MAX_MS = _get_int("ACTION_DELAY_MAX_MS", 1200)
MANUAL_ALLOW_WAIT_SECONDS = max(0, _get_int("MANUAL_ALLOW_WAIT_SECONDS", 8))

# Unknown-stage retry strategy (used when anti-bot scripts delay page readiness).
# Aggressive defaults: more retries and longer waits to let dynamic content load.
UNKNOWN_STAGE_MAX_RETRIES = max(1, _get_int("UNKNOWN_STAGE_MAX_RETRIES", 8))
UNKNOWN_STAGE_RETRY_BASE_MS = max(1000, _get_int("UNKNOWN_STAGE_RETRY_BASE_MS", 6000))
UNKNOWN_STAGE_RETRY_STEP_MS = max(0, _get_int("UNKNOWN_STAGE_RETRY_STEP_MS", 3000))
UNKNOWN_STAGE_RETRY_MAX_MS = max(
    UNKNOWN_STAGE_RETRY_BASE_MS,
    _get_int("UNKNOWN_STAGE_RETRY_MAX_MS", 20000),
)

CHECK_INTERVAL_MIN_SECONDS = _get_int("CHECK_INTERVAL_MIN_SECONDS", 600)
CHECK_INTERVAL_MAX_SECONDS = _get_int("CHECK_INTERVAL_MAX_SECONDS", 1800)
BLOCK_BACKOFF_MIN_SECONDS = max(0, _get_int("BLOCK_BACKOFF_MIN_SECONDS", 300))
BLOCK_BACKOFF_MAX_SECONDS = max(
    BLOCK_BACKOFF_MIN_SECONDS,
    _get_int("BLOCK_BACKOFF_MAX_SECONDS", 900),
)

NOTIFY_COOLDOWN_SECONDS = _get_int("NOTIFY_COOLDOWN_SECONDS", 3600)
NOTIFY_ON_UNAVAILABLE = _get_bool("NOTIFY_ON_UNAVAILABLE", True)
UNAVAILABLE_NOTIFY_COOLDOWN_SECONDS = max(
    0,
    _get_int("UNAVAILABLE_NOTIFY_COOLDOWN_SECONDS", NOTIFY_COOLDOWN_SECONDS),
)

PROXIES_FILE = os.getenv("PROXIES_FILE", "").strip()

_proxies_from_file = _load_proxies_from_file(PROXIES_FILE)
_proxies_from_env = _parse_proxy_list(os.getenv("PROXIES", ""))

# Keep order while dropping duplicates.
PROXIES = list(dict.fromkeys(_proxies_from_file + _proxies_from_env))
# How many checks to run on the same proxy before rotating to the next one.
# Default is 2: proxy A is used for runs 1+2, proxy B for runs 3+4, etc.
# Also controls Oxylabs sticky-session lifetime when OXYLABS_STICKY_SESSION=true.
PROXY_ROTATE_EVERY = max(1, _get_int("PROXY_ROTATE_EVERY", 2))

OXYLABS_ENABLED = _get_bool("OXYLABS_ENABLED", False)
OXYLABS_ENTRY = os.getenv("OXYLABS_ENTRY", "pr.oxylabs.io").strip()
OXYLABS_PORT = _get_int("OXYLABS_PORT", 7777)
OXYLABS_USERNAME = os.getenv("OXYLABS_USERNAME", "").strip()
OXYLABS_PASSWORD = os.getenv("OXYLABS_PASSWORD", "").strip()
OXYLABS_COUNTRY = os.getenv("OXYLABS_COUNTRY", "").strip()
OXYLABS_CITY = os.getenv("OXYLABS_CITY", "").strip()
OXYLABS_STICKY_SESSION = _get_bool("OXYLABS_STICKY_SESSION", True)
OXYLABS_SESSION_TIME_MINUTES = max(0, _get_int("OXYLABS_SESSION_TIME_MINUTES", 0))

RUN_ONCE = _get_bool("RUN_ONCE", False)
DRY_RUN = _get_bool("DRY_RUN", False)

SAVE_DEBUG_ARTIFACTS = _get_bool("SAVE_DEBUG_ARTIFACTS", False)
DEBUG_ARTIFACTS_DIR = os.getenv("DEBUG_ARTIFACTS_DIR", "artifacts")

REQUIRED_CONFIG_VARS = [
    "BOT_TOKEN",
    "CHAT_ID",
    "NIE",
    "NAME",
    "NATIONALITY",
]


def get_missing_required_config(require_telegram=True):
    values = {
        "BOT_TOKEN": BOT_TOKEN,
        "CHAT_ID": CHAT_ID,
        "NIE": NIE,
        "NAME": NAME,
        "NATIONALITY": NATIONALITY,
    }
    required = list(REQUIRED_CONFIG_VARS)
    if not require_telegram:
        required = [key for key in required if key not in {"BOT_TOKEN", "CHAT_ID"}]

    return [key for key in required if not (values.get(key) or "").strip()]


def validate_required_config(require_telegram=True):
    missing = get_missing_required_config(require_telegram=require_telegram)
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    if OXYLABS_ENABLED:
        missing_oxylabs = []
        if not OXYLABS_USERNAME:
            missing_oxylabs.append("OXYLABS_USERNAME")
        if not OXYLABS_PASSWORD:
            missing_oxylabs.append("OXYLABS_PASSWORD")
        if missing_oxylabs:
            raise ValueError(
                "Missing required Oxylabs environment variables: "
                + ", ".join(missing_oxylabs)
            )