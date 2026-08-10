"""
config.py

Loads and validates configuration from environment variables (.env).
No secrets are ever hardcoded here. If required variables are missing,
the process exits with a clear error message before anything else runs.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv is optional at runtime (e.g. if env vars are injected
    # directly by the host / systemd / Docker). We degrade gracefully.
    pass

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

VIDEOS_FILE = DATA_DIR / "videos.json"
CHANNELS_FILE = DATA_DIR / "channels.json"
SCHEDULE_FILE = DATA_DIR / "schedule.json"

VALID_MISSED_POLICIES = {"next", "skip_all"}


def _die(message: str) -> None:
    print(f"[CONFIG ERROR] {message}", file=sys.stderr)
    sys.exit(1)


def _get_int_env(name: str, required: bool = True, default: int | None = None) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        if required:
            _die(f"Missing required environment variable: {name}")
        return default
    try:
        return int(raw.strip())
    except ValueError:
        _die(f"Environment variable {name} must be an integer, got: {raw!r}")
    return default  # unreachable, keeps type checkers happy


def _get_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    bot_token: str
    owner_id: int
    source_channel_id: int
    interval_minutes: int = 30
    total_videos: int = 1440
    shuffle: bool = False
    missed_schedule_policy: str = "next"
    max_retries: int = 3
    retry_backoff_seconds: int = 5


def load_config() -> Config:
    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    if not bot_token:
        _die("Missing required environment variable: BOT_TOKEN")

    owner_id = _get_int_env("OWNER_ID")
    source_channel_id = _get_int_env("SOURCE_CHANNEL_ID")

    interval_minutes = _get_int_env("INTERVAL_MINUTES", required=False, default=30) or 30
    total_videos = _get_int_env("TOTAL_VIDEOS", required=False, default=1440) or 1440
    shuffle = _get_bool_env("SHUFFLE", default=False)

    missed_policy = os.environ.get("MISSED_SCHEDULE_POLICY", "next").strip().lower()
    if missed_policy not in VALID_MISSED_POLICIES:
        _die(
            f"MISSED_SCHEDULE_POLICY must be one of {sorted(VALID_MISSED_POLICIES)}, "
            f"got: {missed_policy!r}"
        )

    if interval_minutes <= 0:
        _die("INTERVAL_MINUTES must be a positive integer")
    if total_videos <= 0:
        _die("TOTAL_VIDEOS must be a positive integer")

    return Config(
        bot_token=bot_token,
        owner_id=owner_id,  # type: ignore[arg-type]
        source_channel_id=source_channel_id,  # type: ignore[arg-type]
        interval_minutes=interval_minutes,
        total_videos=total_videos,
        shuffle=shuffle,
        missed_schedule_policy=missed_policy,
    )


CONFIG = load_config()
