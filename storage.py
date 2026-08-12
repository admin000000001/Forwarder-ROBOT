from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("forwarder")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")

SOURCES_FILE = os.path.join(DATA_DIR, "sources.json")
CHANNELS_FILE = os.path.join(DATA_DIR, "channels.json")
POSTS_FILE = os.path.join(DATA_DIR, "posts.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
SCHEDULE_FILE = os.path.join(DATA_DIR, "schedule.json")

_LOCK = asyncio.Lock()


# ============================================================
# DIRECTORY
# ============================================================

def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


# ============================================================
# DEFAULTS
# ============================================================

DEFAULT_SETTINGS = {
    "interval_minutes": 10,
    "source_mode": "round_robin",
    "missed_schedule_policy": "next",
    "total_posts": 0,
    "routes": [],
}


DEFAULT_SCHEDULE = {
    "running": False,
    "current_index": 0,
    "next_run_iso": None,
    "last_completed_iso": None,
}


# ============================================================
# JSON HELPERS
# ============================================================

def _read_json_sync(path: str, default: Any) -> Any:
    _ensure_data_dir()

    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as exc:
        logger.error(
            "[STORAGE] Failed reading %s: %s",
            path,
            exc,
        )

        return default


def _write_json_sync(path: str, data: Any) -> None:
    _ensure_data_dir()

    temp = path + ".tmp"

    with open(
        temp,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(temp, path)


async def _read(path: str, default: Any) -> Any:
    async with _LOCK:
        return await asyncio.to_thread(
            _read_json_sync,
            path,
            default,
        )


async def _write(path: str, data: Any) -> None:
    async with _LOCK:
        await asyncio.to_thread(
            _write_json_sync,
            path,
            data,
        )


# ============================================================
# SOURCES
# ============================================================

async def get_sources() -> list[dict[str, Any]]:
    data = await _read(
        SOURCES_FILE,
        [],
    )

    if not isinstance(data, list):
        return []

    return data


async def save_sources(
    sources: list[dict[str, Any]],
) -> None:

    clean: list[dict[str, Any]] = []

    seen: set[int] = set()

    for source in sources:

        try:
            chat_id = int(
                source.get("chat_id")
            )
        except Exception:
            continue

        if chat_id in seen:
            continue

        seen.add(chat_id)

        clean.append(
            {
                "chat_id": chat_id,
                "title": source.get(
                    "title",
                    "Unknown",
                ),
                "username": source.get(
                    "username"
                ),
                "enabled": bool(
                    source.get(
                        "enabled",
                        True,
                    )
                ),
            }
        )

    await _write(
        SOURCES_FILE,
        clean,
    )


async def add_source(
    chat_id: int,
    title: str | None = None,
    username: str | None = None,
) -> bool:

    sources = await get_sources()

    for source in sources:
        try:
            if int(source["chat_id"]) == int(chat_id):
                return False
        except Exception:
            continue

    sources.append(
        {
            "chat_id": int(chat_id),
            "title": title or "Unknown",
            "username": username,
            "enabled": True,
        }
    )

    await save_sources(sources)

    return True


async def remove_source(
    chat_id: int,
) -> bool:

    sources = await get_sources()

    new_sources = []

    removed = False

    for source in sources:

        try:
            sid = int(source["chat_id"])
        except Exception:
            continue

        if sid == int(chat_id):
            removed = True
            continue

        new_sources.append(source)

    await save_sources(new_sources)

    return removed


# ============================================================
# DESTINATIONS
# ============================================================

async def get_channels() -> list[dict[str, Any]]:
    data = await _read(
        CHANNELS_FILE,
        [],
    )

    if not isinstance(data, list):
        return []

    return data


async def save_channels(
    channels: list[dict[str, Any]],
) -> None:

    clean: list[dict[str, Any]] = []

    seen: set[int] = set()

    for channel in channels:

        try:
            chat_id = int(
                channel.get("chat_id")
            )
        except Exception:
            continue

        if chat_id in seen:
            continue

        seen.add(chat_id)

        clean.append(
            {
                "chat_id": chat_id,
                "title": channel.get(
                    "title",
                    "Unknown",
                ),
                "username": channel.get(
                    "username"
                ),
                "enabled": bool(
                    channel.get(
                        "enabled",
                        True,
                    )
                ),
            }
        )

    await _write(
        CHANNELS_FILE,
        clean,
    )


async def add_channel(
    chat_id: int,
    title: str | None = None,
    username: str | None = None,
) -> bool:

    channels = await get_channels()

    for channel in channels:

        try:
            if int(channel["chat_id"]) == int(chat_id):
                return False
        except Exception:
            continue

    channels.append(
        {
            "chat_id": int(chat_id),
            "title": title or "Unknown",
            "username": username,
            "enabled": True,
        }
    )

    await save_channels(channels)

    return True


async def remove_channel(
    chat_id: int,
) -> bool:

    channels = await get_channels()

    new_channels = []

    removed = False

    for channel in channels:

        try:
            cid = int(channel["chat_id"])
        except Exception:
            continue

        if cid == int(chat_id):
            removed = True
            continue

        new_channels.append(channel)

    await save_channels(new_channels)

    return removed


# ============================================================
# POSTS
# ============================================================

async def get_posts() -> list[dict[str, Any]]:
    data = await _read(
        POSTS_FILE,
        [],
    )

    if not isinstance(data, list):
        return []

    return data


async def save_posts(
    posts: list[dict[str, Any]],
) -> None:

    await _write(
        POSTS_FILE,
        posts,
    )


async def add_post(
    post: dict[str, Any],
) -> bool:
    """
    Add one post.

    Duplicate detection:
        source_chat_id + message_ids
    """

    posts = await get_posts()

    source_id = post.get(
        "source_chat_id"
    )

    message_ids = post.get(
        "message_ids",
        [],
    )

    if not message_ids:

        message_id = post.get(
            "message_id"
        )

        if message_id is not None:
            message_ids = [
                message_id
            ]

    try:
        source_id = int(source_id)
        message_ids = [
            int(x)
            for x in message_ids
        ]
    except Exception:
        return False

    if not message_ids:
        return False

    # --------------------------------------------------------
    # Duplicate check
    # --------------------------------------------------------

    for existing in posts:

        try:
            existing_source = int(
                existing.get(
                    "source_chat_id"
                )
            )

            existing_ids = [
                int(x)
                for x in existing.get(
                    "message_ids",
                    [],
                )
            ]

        except Exception:
            continue

        if (
            existing_source == source_id
            and existing_ids == message_ids
        ):
            return False

    post["source_chat_id"] = source_id
    post["message_ids"] = message_ids

    if "created_at" not in post:
        post["created_at"] = now_iso()

    posts.append(post)

    await save_posts(posts)

    return True


async def clear_posts() -> None:
    await save_posts([])


# ============================================================
# SETTINGS
# ============================================================

async def get_settings() -> dict[str, Any]:

    data = await _read(
        SETTINGS_FILE,
        {},
    )

    if not isinstance(data, dict):
        data = {}

    result = dict(DEFAULT_SETTINGS)

    result.update(data)

    if not isinstance(
        result.get("routes"),
        list,
    ):
        result["routes"] = []

    return result


async def save_settings(
    settings: dict[str, Any],
) -> None:

    current = dict(DEFAULT_SETTINGS)

    current.update(settings)

    await _write(
        SETTINGS_FILE,
        current,
    )


# ============================================================
# ROUTES
# ============================================================

async def get_routes() -> list[dict[str, Any]]:

    settings = await get_settings()

    routes = settings.get(
        "routes",
        [],
    )

    if not isinstance(routes, list):
        return []

    clean: list[dict[str, Any]] = []

    for route in routes:

        if not isinstance(route, dict):
            continue

        try:
            source_id = int(
                route.get("source_id")
            )
        except Exception:
            continue

        destinations = []

        for destination in route.get(
            "destinations",
            [],
        ):

            try:
                destinations.append(
                    int(destination)
                )
            except Exception:
                continue

        clean.append(
            {
                "source_id": source_id,
                "destinations": list(
                    dict.fromkeys(
                        destinations
                    )
                ),
            }
        )

    return clean


async def save_routes(
    routes: list[dict[str, Any]],
) -> None:

    settings = await get_settings()

    clean: list[dict[str, Any]] = []

    for route in routes:

        try:
            source_id = int(
                route.get("source_id")
            )
        except Exception:
            continue

        destinations = []

        for destination in route.get(
            "destinations",
            [],
        ):

            try:
                destinations.append(
                    int(destination)
                )
            except Exception:
                continue

        clean.append(
            {
                "source_id": source_id,
                "destinations": list(
                    dict.fromkeys(
                        destinations
                    )
                ),
            }
        )

    settings["routes"] = clean

    await save_settings(settings)


# ============================================================
# SCHEDULE
# ============================================================

async def get_schedule() -> dict[str, Any]:

    data = await _read(
        SCHEDULE_FILE,
        {},
    )

    if not isinstance(data, dict):
        data = {}

    result = dict(
        DEFAULT_SCHEDULE
    )

    result.update(data)

    return result


async def save_schedule(
    schedule: dict[str, Any],
) -> None:

    current = dict(
        DEFAULT_SCHEDULE
    )

    current.update(schedule)

    await _write(
        SCHEDULE_FILE,
        current,
    )


# ============================================================
# TIME
# ============================================================

def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()
