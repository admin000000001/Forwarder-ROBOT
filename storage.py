"""
storage.py

JSON persistence layer for Forwarder-ROBOT.

Stores:
    data/sources.json
    data/channels.json
    data/posts.json
    data/schedule.json
    data/settings.json

Features:
    - Atomic writes
    - Corrupt JSON recovery
    - Async-safe file access
    - Multiple source channels
    - Multiple destination channels
    - Source -> destination routes
    - Post deduplication
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any


# ============================================================
# PATHS
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

SOURCES_FILE = os.path.join(DATA_DIR, "sources.json")
CHANNELS_FILE = os.path.join(DATA_DIR, "channels.json")
POSTS_FILE = os.path.join(DATA_DIR, "posts.json")
SCHEDULE_FILE = os.path.join(DATA_DIR, "schedule.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")


# ============================================================
# DEFAULT DATA
# ============================================================

_DEFAULTS = {
    SOURCES_FILE: {
        "sources": []
    },

    CHANNELS_FILE: {
        "channels": []
    },

    POSTS_FILE: {
        "posts": []
    },

    SCHEDULE_FILE: {
        "current_index": 0,
        "running": False,
        "next_run_iso": None,
        "last_completed_iso": None,
    },

    SETTINGS_FILE: {
        "interval_minutes": None,
        "source_mode": None,
        "missed_schedule_policy": None,
        "auto_queue_new_posts": True,
        "total_posts": None,

        # Source -> destination routes
        "routes": []
    }
}


# ============================================================
# LOCK
# ============================================================

_LOCK = asyncio.Lock()


# ============================================================
# FILE HELPERS
# ============================================================

def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _clone_default(path: str) -> dict[str, Any]:
    return json.loads(
        json.dumps(_DEFAULTS[path])
    )


def _atomic_write(
    path: str,
    data: dict[str, Any]
) -> None:

    _ensure_data_dir()

    directory = os.path.dirname(path)

    fd, tmp_path = tempfile.mkstemp(
        prefix=".tmp_",
        dir=directory
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )

            f.flush()
            os.fsync(f.fileno())

        os.replace(
            tmp_path,
            path
        )

    except Exception:

        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass

        raise


def _read_raw(
    path: str
) -> dict[str, Any]:

    _ensure_data_dir()

    if not os.path.exists(path):

        default = _clone_default(path)

        _atomic_write(
            path,
            default
        )

        return default

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            content = f.read().strip()

        if not content:
            raise ValueError("empty JSON file")

        data = json.loads(content)

        if not isinstance(data, dict):
            raise ValueError(
                "JSON root must be an object"
            )

        return data

    except (
        json.JSONDecodeError,
        ValueError
    ) as exc:

        print(
            f"[WARNING] Corrupt JSON detected: "
            f"{path}: {exc}"
        )

        # Backup corrupt file
        try:

            backup = (
                path
                + ".corrupt."
                + datetime.now().strftime(
                    "%Y%m%d_%H%M%S"
                )
            )

            os.replace(
                path,
                backup
            )

        except OSError:
            pass

        default = _clone_default(path)

        _atomic_write(
            path,
            default
        )

        return default


async def read_json(
    path: str
) -> dict[str, Any]:

    async with _LOCK:

        return await asyncio.to_thread(
            _read_raw,
            path
        )


async def write_json(
    path: str,
    data: dict[str, Any]
) -> None:

    async with _LOCK:

        await asyncio.to_thread(
            _atomic_write,
            path,
            data
        )


# ============================================================
# COMMON
# ============================================================

def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def new_uid() -> str:
    return uuid.uuid4().hex


# ============================================================
# SOURCES
# ============================================================

async def get_sources() -> list[dict[str, Any]]:

    data = await read_json(
        SOURCES_FILE
    )

    sources = data.get(
        "sources",
        []
    )

    if not isinstance(
        sources,
        list
    ):
        return []

    return sources


async def save_sources(
    sources: list[dict[str, Any]]
) -> None:

    await write_json(
        SOURCES_FILE,
        {
            "sources": sources
        }
    )


async def add_source(
    source: dict[str, Any]
) -> bool:

    sources = await get_sources()

    chat_id = int(
        source["chat_id"]
    )

    for item in sources:

        if int(
            item.get("chat_id")
        ) == chat_id:

            return False

    source.setdefault(
        "enabled",
        True
    )

    sources.append(source)

    await save_sources(
        sources
    )

    return True


async def remove_source(
    chat_id: int
) -> bool:

    sources = await get_sources()

    old_len = len(sources)

    sources = [
        s for s in sources
        if int(s.get("chat_id"))
        != int(chat_id)
    ]

    if len(sources) == old_len:
        return False

    await save_sources(
        sources
    )

    return True


# ============================================================
# DESTINATIONS
# ============================================================

async def get_channels() -> list[dict[str, Any]]:

    data = await read_json(
        CHANNELS_FILE
    )

    channels = data.get(
        "channels",
        []
    )

    if not isinstance(
        channels,
        list
    ):
        return []

    return channels


async def save_channels(
    channels: list[dict[str, Any]]
) -> None:

    await write_json(
        CHANNELS_FILE,
        {
            "channels": channels
        }
    )


async def add_channel(
    channel: dict[str, Any]
) -> bool:

    channels = await get_channels()

    chat_id = int(
        channel["chat_id"]
    )

    for item in channels:

        if int(
            item.get("chat_id")
        ) == chat_id:

            return False

    channel.setdefault(
        "enabled",
        True
    )

    channels.append(channel)

    await save_channels(
        channels
    )

    return True


async def remove_channel(
    chat_id: int
) -> bool:

    channels = await get_channels()

    old_len = len(channels)

    channels = [
        c for c in channels
        if int(c.get("chat_id"))
        != int(chat_id)
    ]

    if len(channels) == old_len:
        return False

    await save_channels(
        channels
    )

    return True


# ============================================================
# POSTS
# ============================================================

async def get_posts() -> list[dict[str, Any]]:

    data = await read_json(
        POSTS_FILE
    )

    posts = data.get(
        "posts",
        []
    )

    if not isinstance(
        posts,
        list
    ):
        return []

    return posts


async def save_posts(
    posts: list[dict[str, Any]]
) -> None:

    await write_json(
        POSTS_FILE,
        {
            "posts": posts
        }
    )


def _post_key(
    source_chat_id: int,
    message_ids: list[int]
) -> str:

    ids = ",".join(
        str(x)
        for x in sorted(message_ids)
    )

    return (
        f"{int(source_chat_id)}:"
        f"{ids}"
    )


async def post_exists(
    source_chat_id: int,
    message_ids: list[int]
) -> bool:

    key = _post_key(
        source_chat_id,
        message_ids
    )

    posts = await get_posts()

    for post in posts:

        if post.get("_key") == key:
            return True

    return False


async def add_post(
    post: dict[str, Any]
) -> bool:

    if "source_chat_id" not in post:
        return False

    message_ids = post.get(
        "message_ids",
        []
    )

    if not isinstance(
        message_ids,
        list
    ):
        return False

    if not message_ids:
        return False

    source_id = int(
        post["source_chat_id"]
    )

    normalized_ids = []

    for message_id in message_ids:

        try:
            normalized_ids.append(
                int(message_id)
            )
        except (
            TypeError,
            ValueError
        ):
            continue

    if not normalized_ids:
        return False

    post["source_chat_id"] = source_id
    post["message_ids"] = normalized_ids

    key = _post_key(
        source_id,
        normalized_ids
    )

    posts = await get_posts()

    for existing in posts:

        if existing.get("_key") == key:
            return False

    post["_key"] = key

    post.setdefault(
        "uid",
        new_uid()
    )

    post.setdefault(
        "created_at",
        now_iso()
    )

    post.setdefault(
        "type",
        "message"
    )

    posts.append(
        post
    )

    await save_posts(
        posts
    )

    return True


async def clear_posts() -> None:

    await save_posts([])


# ============================================================
# SCHEDULE
# ============================================================

async def get_schedule() -> dict[str, Any]:

    data = await read_json(
        SCHEDULE_FILE
    )

    merged = _clone_default(
        SCHEDULE_FILE
    )

    merged.update(data)

    return merged


async def save_schedule(
    schedule: dict[str, Any]
) -> None:

    await write_json(
        SCHEDULE_FILE,
        schedule
    )


# ============================================================
# SETTINGS
# ============================================================

async def get_settings() -> dict[str, Any]:

    data = await read_json(
        SETTINGS_FILE
    )

    merged = _clone_default(
        SETTINGS_FILE
    )

    merged.update(data)

    return merged


async def save_settings(
    settings: dict[str, Any]
) -> None:

    await write_json(
        SETTINGS_FILE,
        settings
    )


# ============================================================
# ROUTES
# ============================================================

async def get_routes() -> list[dict[str, Any]]:

    settings = await get_settings()

    routes = settings.get(
        "routes",
        []
    )

    if not isinstance(
        routes,
        list
    ):
        return []

    return routes


async def save_routes(
    routes: list[dict[str, Any]]
) -> None:

    settings = await get_settings()

    settings["routes"] = routes

    await save_settings(
        settings
    )


async def add_route(
    source_id: int,
    destination_id: int
) -> bool:

    routes = await get_routes()

    source_id = int(source_id)
    destination_id = int(destination_id)

    route = None

    for item in routes:

        try:
            if int(
                item.get("source_id")
            ) == source_id:

                route = item
                break

        except (
            TypeError,
            ValueError
        ):
            continue

    if route is None:

        route = {
            "source_id": source_id,
            "destinations": []
        }

        routes.append(route)

    destinations = route.setdefault(
        "destinations",
        []
    )

    destinations = [
        int(x)
        for x in destinations
    ]

    if destination_id in destinations:
        return False

    destinations.append(
        destination_id
    )

    route["destinations"] = destinations

    await save_routes(
        routes
    )

    return True


async def remove_route(
    source_id: int,
    destination_id: int
) -> bool:

    routes = await get_routes()

    source_id = int(source_id)
    destination_id = int(destination_id)

    changed = False

    for route in routes:

        try:
            same_source = (
                int(
                    route.get("source_id")
                )
                == source_id
            )
        except (
            TypeError,
            ValueError
        ):
            continue

        if not same_source:
            continue

        destinations = route.get(
            "destinations",
            []
        )

        new_destinations = [
            int(x)
            for x in destinations
            if int(x) != destination_id
        ]

        if len(
            new_destinations
        ) != len(destinations):

            route["destinations"] = (
                new_destinations
            )

            changed = True

    routes = [
        r for r in routes
        if r.get("destinations")
    ]

    await save_routes(
        routes
    )

    return changed


async def get_destinations_for_source(
    source_id: int
) -> list[int]:

    routes = await get_routes()

    source_id = int(source_id)

    for route in routes:

        try:

            if int(
                route.get("source_id")
            ) == source_id:

                destinations = route.get(
                    "destinations",
                    []
                )

                return [
                    int(x)
                    for x in destinations
                ]

        except (
            TypeError,
            ValueError
        ):
            continue

    return []
