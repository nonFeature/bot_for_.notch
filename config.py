import os

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

def _parse_admin_ids(raw: str) -> tuple[int, ...]:
    ids: list[int] = []
    for part in raw.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        try:
            ids.append(int(chunk))
        except ValueError:
            continue
    return tuple(ids)

ADMIN_IDS = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
LOG_ENABLED = _env_bool("LOG_ENABLED", True)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"