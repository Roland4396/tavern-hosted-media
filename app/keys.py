import hashlib
import logging
from typing import Any
from fastapi import HTTPException
from .settings import settings

logger=logging.getLogger("wiro-service")

def current_api_key() -> tuple[str, str]:
    if settings.api_key_file is not None:
        try:
            api_key = settings.api_key_file.read_text().strip()
        except FileNotFoundError:
            api_key = ""
        except OSError as exc:
            logger.warning("Failed to read Wiro API key file %s: %s", settings.api_key_file, exc)
            api_key = ""
        if api_key:
            return api_key, f"file:{settings.api_key_file}"

    if settings.api_key:
        return settings.api_key, "env:WIRO_API_KEY"

    return "", "none"


def require_api_key() -> str:
    api_key, source = current_api_key()
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="Wiro API key is not configured",
        )
    logger.debug("Using Wiro API key from %s", source)
    return api_key


def key_fingerprint(api_key: str) -> str | None:
    if not api_key:
        return None
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def key_metadata() -> dict[str, Any]:
    api_key, source = current_api_key()
    return {
        "configured": bool(api_key),
        "source": source,
        "fingerprint": key_fingerprint(api_key),
        "length": len(api_key) if api_key else 0,
    }


def write_api_key(api_key: str) -> dict[str, Any]:
    if settings.api_key_file is None:
        raise HTTPException(
            status_code=500,
            detail="WIRO_API_KEY_FILE is not configured",
        )

    settings.api_key_file.parent.mkdir(parents=True, exist_ok=True)
    settings.api_key_file.write_text(f"{api_key.strip()}\n")
    try:
        settings.api_key_file.chmod(0o600)
    except OSError as exc:
        logger.warning("Failed to chmod Wiro API key file %s: %s", settings.api_key_file, exc)
    return key_metadata()


