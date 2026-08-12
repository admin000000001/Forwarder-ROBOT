"""
storage.py
Persistent JSON storage for Telegram Forwarder Bot.

Files:
    sources.json
    channels.json
    routes.json
    posts.json
    schedule.json
    settings.json
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SOURCES_FILE = os.path.join(BASE_DIR, "sources.json")
CHANNELS_FILE = os.path.join(BASE_DIR, "channels.json")
ROUTES_FILE = os.path.join(BASE_DIR, "routes.json")
POSTS_FILE = os.path.join(BASE_DIR, "posts.json")
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

_LOCK = asyncio.Lock()


# ============================================================
# DEFAULTS
# ============================================================

DEFAULT_SCHEDULE = {
    "running": False,
    "current_index": 0,
    "next_run_iso": None,
    "last_completed_iso": None,
}

DEFAULT_SETTINGS = {
    "interval_minutes": 10,
    "source_mode": "round_robin",
    "missed_schedule_policy": "skip",
    "total_posts": 0,
}


# ============================================================
# JSON HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json_sync(
    path: str,
    default: Any,
) -> Any:

    if not os.path.exists(path):
        return default

    try:
        with open(
            path,
            "r",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    except Exception:
        return default


def _write_json_sync(
    path: str,
    data: Any,
) -> None:

    directory = os.path.dirname(path)

    os.makedirs(
        directory,
        exist_ok=True,
    )

    # Atomic write
    fd, temp_path = tempfile.mkstemp(
        prefix=".tmp_",
        suffix=".json",
        dir=directory,
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False,
            )

            f.flush()
            os.fsync(f.fileno())

        os.replace(
            temp_path,
            path,
        )

    finally:

        if os.path.exists(temp_path):

            try:
                os.remove(temp_path)
            except Exception:
                pass


async def _read(
    path: str,
    default: Any,
) -> Any:

    async with _LOCK:

        return await asyncio.to_thread(
            _read_json_sync,
            path,
            default,
        )


async def _write(
    path: str,
    data: Any,
) -> None:

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

    await _write(
        SOURCES_FILE,
        sources,
    )


async def add_source(
    chat_id: int,
    title: str | None = None,
    username: str | None = None,
) -> tuple[bool, str]:

    chat_id = int(chat_id)

    sources = await get_sources()

    for source in sources:

        if int(source.get("chat_id", 0)) == chat_id:

            source["enabled"] = True

            if title:
                source["title"] = title

            if username:
                source["username"] = username

            await save_sources(sources)

            return False, "Source already exists."

    sources.append(
        {
            "chat_id": chat_id,
            "title": title or str(chat_id),
            "username": username,
            "enabled": True,
        }
    )

    await save_sources(sources)

    return True, "Source added successfully."


async def remove_source(
    chat_id: int,
) -> bool:

    chat_id = int(chat_id)

    sources = await get_sources()

    old_len = len(sources)

    sources = [
        s
        for s in sources
        if int(s.get("chat_id", 0)) != chat_id
    ]

    if len(sources) == old_len:
        return False

    await save_sources(sources)

    # Also remove route
    routes = await get_routes()

    routes = [
        r
        for r in routes
        if int(r.get("source_id", 0)) != chat_id
    ]

    await save_routes(routes)

    return True


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

    await _write(
        CHANNELS_FILE,
        channels,
    )


async def add_channel(
    chat_id: int,
    title: str | None = None,
    username: str | None = None,
) -> tuple[bool, str]:

    chat_id = int(chat_id)

    channels = await get_channels()

    for channel in channels:

        if int(channel.get("chat_id", 0)) == chat_id:

            channel["enabled"] = True

            if title:
                channel["title"] = title

            if username:
                channel["username"] = username

            await save_channels(channels)

            return False, "Destination already exists."

    channels.append(
        {
            "chat_id": chat_id,
            "title": title or str(chat_id),
            "username": username,
            "enabled": True,
        }
    )

    await save_channels(channels)

    return True, "Destination added successfully."


async def remove_channel(
    chat_id: int,
) -> bool:

    chat_id = int(chat_id)

    channels = await get_channels()

    old_len = len(channels)

    channels = [
        c
        for c in channels
        if int(c.get("chat_id", 0)) != chat_id
    ]

    if len(channels) == old_len:
        return False

    await save_channels(channels)

    # Remove this destination from all routes
    routes = await get_routes()

    changed = False

    for route in routes:

        old_destinations = route.get(
            "destinations",
            [],
        )

        new_destinations = [
            int(x)
            for x in old_destinations
            if int(x) != chat_id
        ]

        if new_destinations != old_destinations:
            changed = True

        route["destinations"] = new_destinations

    if changed:
        await save_routes(routes)

    return True


# ============================================================
# ROUTES
# ============================================================

async def get_routes() -> list[dict[str, Any]]:

    data = await _read(
        ROUTES_FILE,
        [],
    )

    if not isinstance(data, list):
        return []

    # Normalize
    normalized = []

    for route in data:

        try:
            source_id = int(
                route.get("source_id")
            )
        except Exception:
            continue

        destinations = []

        for dest in route.get(
            "destinations",
            [],
        ):

            try:
                destinations.append(int(dest))
            except Exception:
                pass

        normalized.append(
            {
                "source_id": source_id,
                "destinations": list(
                    dict.fromkeys(destinations)
                ),
            }
        )

    return normalized


async def save_routes(
    routes: list[dict[str, Any]],
) -> None:

    await _write(
        ROUTES_FILE,
        routes,
    )


async def add_route(
    source_id: int,
    destination_id: int,
) -> tuple[bool, str]:

    source_id = int(source_id)
    destination_id = int(destination_id)

    routes = await get_routes()

    route = None

    for item in routes:

        if int(item["source_id"]) == source_id:

            route = item
            break

    if route is None:

        route = {
            "source_id": source_id,
            "destinations": [],
        }

        routes.append(route)

    if destination_id in route["destinations"]:

        return False, "Route already exists."

    route["destinations"].append(
        destination_id
    )

    await save_routes(routes)

    return True, "Route added successfully."


async def remove_route(
    source_id: int,
    destination_id: int,
) -> bool:

    source_id = int(source_id)
    destination_id = int(destination_id)

    routes = await get_routes()

    changed = False

    for route in routes:

        if int(route["source_id"]) != source_id:
            continue

        old = route.get(
            "destinations",
            [],
        )

        new = [
            int(x)
            for x in old
            if int(x) != destination_id
        ]

        if new != old:
            changed = True

        route["destinations"] = new

    if changed:
        await save_routes(routes)

    return changed


async def clear_routes() -> None:

    await save_routes([])


async def get_destinations_for_source(
    source_id: int,
) -> list[int]:

    source_id = int(source_id)

    routes = await get_routes()

    for route in routes:

        if int(route["source_id"]) == source_id:

            return [
                int(x)
                for x in route.get(
                    "destinations",
                    [],
                )
            ]

    return []


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
    source_chat_id: int,
    message_id: int,
    message_type: str = "message",
    media_group_id: str | None = None,
    caption: str | None = None,
) -> bool:

    source_chat_id = int(source_chat_id)
    message_id = int(message_id)

    posts = await get_posts()

    # Do not duplicate
    for post in posts:

        if int(
            post.get(
                "source_chat_id",
                0,
            )
        ) != source_chat_id:
            continue

        ids = post.get(
            "message_ids",
            [],
        )

        if message_id in [
            int(x)
            for x in ids
        ]:
            return False

    posts.append(
        {
            "source_chat_id": source_chat_id,
            "message_ids": [
                message_id
            ],
            "message_id": message_id,
            "type": message_type,
            "media_group_id": media_group_id,
            "caption": caption,
            "created_at": now_iso(),
        }
    )

    await save_posts(posts)

    return True


async def add_album_post(
    source_chat_id: int,
    message_ids: list[int],
    media_group_id: str,
    message_type: str = "album",
) -> bool:

    source_chat_id = int(source_chat_id)

    message_ids = [
        int(x)
        for x in message_ids
    ]

    if not message_ids:
        return False

    posts = await get_posts()

    for post in posts:

        if int(
            post.get(
                "source_chat_id",
                0,
            )
        ) != source_chat_id:
            continue

        if (
            post.get("media_group_id")
            == media_group_id
        ):
            return False

    posts.append(
        {
            "source_chat_id": source_chat_id,
            "message_ids": message_ids,
            "message_id": message_ids[0],
            "type": message_type,
            "media_group_id": media_group_id,
            "caption": None,
            "created_at": now_iso(),
        }
    )

    await save_posts(posts)

    return True


async def clear_posts() -> None:

    await save_posts([])


# ============================================================
# SCHEDULE
# ============================================================

async def get_schedule() -> dict[str, Any]:

    data = await _read(
        SCHEDULE_FILE,
        DEFAULT_SCHEDULE.copy(),
    )

    if not isinstance(data, dict):
        data = DEFAULT_SCHEDULE.copy()

    result = DEFAULT_SCHEDULE.copy()
    result.update(data)

    return result


async def save_schedule(
    schedule: dict[str, Any],
) -> None:

    await _write(
        SCHEDULE_FILE,
        schedule,
    )


# ============================================================
# SETTINGS
# ============================================================

async def get_settings() -> dict[str, Any]:

    data = await _read(
        SETTINGS_FILE,
        DEFAULT_SETTINGS.copy(),
    )

    if not isinstance(data, dict):
        data = {}

    result = DEFAULT_SETTINGS.copy()
    result.update(data)

    return result


async def save_settings(
    settings: dict[str, Any],
) -> None:

    await _write(
        SETTINGS_FILE,
        settings,
    )


# ============================================================
# RESET
# ============================================================

async def clear_sources() -> None:

    await save_sources([])

    await clear_routes()


async def clear_channels() -> None:

    await save_channels([])

    routes = await get_routes()

    for route in routes:
        route["destinations"] = []

    await save_routes(routes)
