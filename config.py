from __future__ import annotations

import os
import sys
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


VALID_MISSED_POLICIES = {"next", "skip_all"}
VALID_SOURCE_MODES = {"sequential", "round_robin"}


def _die(message: str) -> None:
    print(f"[CONFIG ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def _get_required(name: str) -> str:
    value = os.getenv(name)

    if value is None or not value.strip():
        _die(f"Missing required environment variable: {name}")

    return value.strip()


def _get_int(name: str, default: int | None = None) -> int:
    value = os.getenv(name)

    if value is None or not value.strip():
        if default is not None:
            return default

        _die(f"Missing required environment variable: {name}")

    try:
        return int(value.strip())
    except ValueError:
        _die(
            f"Environment variable {name} must be an integer, "
            f"got: {value!r}"
        )

    raise RuntimeError("unreachable")


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int

    interval_minutes: int = 30
    total_posts: int = 1440

    source_mode: str = "round_robin"
    missed_schedule_policy: str = "next"

    max_retries: int = 3
    retry_backoff_seconds: int = 5

    auto_queue_new_posts: bool = True


def load_config() -> Config:

    # ---------------------------------------------------------
    # Required
    # ---------------------------------------------------------

    bot_token = _get_required("BOT_TOKEN")
    owner_id = _get_int("OWNER_ID")

    # ---------------------------------------------------------
    # Optional
    # ---------------------------------------------------------

    interval_minutes = _get_int(
        "INTERVAL_MINUTES",
        30,
    )

    total_posts = _get_int(
        "TOTAL_POSTS",
        1440,
    )

    source_mode = os.getenv(
        "SOURCE_MODE",
        "round_robin",
    ).strip().lower()

    missed_schedule_policy = os.getenv(
        "MISSED_SCHEDULE_POLICY",
        "next",
    ).strip().lower()

    max_retries = _get_int(
        "MAX_RETRIES",
        3,
    )

    retry_backoff_seconds = _get_int(
        "RETRY_BACKOFF_SECONDS",
        5,
    )

    auto_queue_new_posts = _get_bool(
        "AUTO_QUEUE_NEW_POSTS",
        True,
    )

    # ---------------------------------------------------------
    # Validation
    # ---------------------------------------------------------

    if interval_minutes <= 0:
        _die("INTERVAL_MINUTES must be greater than 0")

    if total_posts <= 0:
        _die("TOTAL_POSTS must be greater than 0")

    if max_retries <= 0:
        _die("MAX_RETRIES must be greater than 0")

    if retry_backoff_seconds < 0:
        _die("RETRY_BACKOFF_SECONDS cannot be negative")

    if source_mode not in VALID_SOURCE_MODES:
        _die(
            "SOURCE_MODE must be one of: "
            + ", ".join(sorted(VALID_SOURCE_MODES))
        )

    if missed_schedule_policy not in VALID_MISSED_POLICIES:
        _die(
            "MISSED_SCHEDULE_POLICY must be one of: "
            + ", ".join(sorted(VALID_MISSED_POLICIES))
        )

    return Config(
        bot_token=bot_token,
        owner_id=owner_id,
        interval_minutes=interval_minutes,
        total_posts=total_posts,
        source_mode=source_mode,
        missed_schedule_policy=missed_schedule_policy,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        auto_queue_new_posts=auto_queue_new_posts,
    )


CONFIG = load_config()
