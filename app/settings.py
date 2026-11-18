from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    api_key: str
    api_key_file: Path | None
    base_url: str
    model: str
    output_dir: Path
    public_base_url: str
    request_timeout: float
    poll_interval: float
    task_timeout: int
    max_download_bytes: int

    @classmethod
    def from_env(cls) -> "Settings":
        output_dir = Path(os.getenv("WIRO_OUTPUT_DIR", "/app/data/images"))
        api_key_file = os.getenv("WIRO_API_KEY_FILE", "/app/config/api-key").strip()
        return cls(
            api_key=os.getenv("WIRO_API_KEY", "").strip(),
            api_key_file=Path(api_key_file) if api_key_file else None,
            base_url=os.getenv("WIRO_BASE_URL", "https://api.wiro.ai/v1").rstrip("/"),
            model=os.getenv("WIRO_MODEL", "bytedance/seedream-v4-5-uncensored").strip("/"),
            output_dir=output_dir,
            public_base_url=os.getenv("WIRO_PUBLIC_BASE_URL", "http://127.0.0.1:3010").rstrip("/"),
            request_timeout=float(os.getenv("WIRO_REQUEST_TIMEOUT", "120")),
            poll_interval=float(os.getenv("WIRO_POLL_INTERVAL", "2")),
            task_timeout=int(os.getenv("WIRO_TASK_TIMEOUT", "900")),
            max_download_bytes=int(os.getenv("WIRO_MAX_DOWNLOAD_MB", "80")) * 1024 * 1024,
        )


settings = Settings.from_env()

