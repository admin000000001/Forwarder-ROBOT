"""
storage.py

JSON persistence for Forwarder-ROBOT.

Files:
    data/sources.json
    data/channels.json
    data/routes.json
    data/posts.json
    data/schedule.json
    data/settings.json
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

SOURCES_FILE = os.path.join(DATA_DIR, "sources.json")
CHANNELS_FILE = os.path.join(DATA_DIR, "channels.json")
ROUTES_FILE = os.path.join(DATA_DIR, "routes.json")
POSTS_FILE = os.path.join(DATA_DIR, "posts.json")
SCHEDULE_FILE = os.path.join(DATA_DIR, "schedule.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")


DEFAULTS = {
    SOURCES_FILE: {
        "sources": []
    },

    CHANNELS_FILE: {
        "channels": []
    },

    ROUTES_FILE: {
        "routes": []
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
    },
}


_LOCK = asyncio.Lock()


# ============================================================
# BASIC FILE HELPERS
# ============================================================

def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _atomic_write(path: str, data: dict[str, Any]) -> None:
    _ensure_data_dir()

    directory = os.path.dirname(path)

    fd, tmp_path = tempfile.mkstemp(
        prefix=".tmp_",
        dir=directory,
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                data,
                file,
                indent=2,
                ensure_ascii=False,
            )

            file.flush()
            os.fsync(file.fileno())

        os.replace(
            tmp_path,
            path,
        )

    except Exception:

        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass

        raise


def _read_raw(path: str) -> dict[str, Any]:
    _ensure_data_dir()

    if not os.path.exists(path):

        default = _clone(DEFAULTS[path])

        _atomic_write(
            path,
            default,
        )

        return default

    try:

        with open(
            path,
            "r",
            encoding="utf-8",
        ) as file:

            content = file.read().strip()

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
        ValueError,
    ) as exc:

        print(
            f"[WARNING] Corrupt JSON: {path}: {exc}"
        )

        backup = path + ".corrupt"

        try:
            if os.path.exists(path):
                os.replace(
                    path,
                    backup,
                )
        except OSError:
            pass

        default = _clone(
            DEFAULTS[path]
        )

        _atomic_write(
            path,
            default,
        )

        return default


async def read_json(
    path: str,
) -> dict[str, Any]:

    async with _LOCK:

        return await asyncio.to_thread(
            _read_raw,
            path,
        )


async def write_json(
    path: str,
    data: dict[str, Any],
) -> None:

    async with _LOCK:

        await asyncio.to_thread(
            _atomic_write,
            path,
            data,
        )


# ============================================================
# GENERAL
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
        [],
    )

    return (
        sources
        if isinstance(sources, list)
        else []
    )


async def save_sources(
    sources: list[dict[str, Any]],
) -> None:

    await write_json(
        SOURCES_FILE,
        {
            "sources": sources
        },
    )


# ============================================================
# DESTINATIONS
# ============================================================

async def get_channels() -> list[dict[str, Any]]:

    data = await read_json(
        CHANNELS_FILE
    )

    channels = data.get(
        "channels",
        [],
    )

    return (
        channels
        if isinstance(channels, list)
        else []
    )


async def save_channels(
    channels: list[dict[str, Any]],
) -> None:

    await write_json(
        CHANNELS_FILE,
        {
            "channels": channels
        },
    )


# ============================================================
# ROUTES
# ============================================================

async def get_routes() -> list[dict[str, Any]]:
    """
    Return ONLY explicitly configured routes.

    Example:

    [
        {
            "source_id": -100111,
            "destinations": [
                -100211,
                -100212
            ]
        },
        {
            "source_id": -100112,
            "destinations": [
                -100221,
                -100222
            ]
        }
    ]
    """

    data = await read_json(
        ROUTES_FILE
    )

    routes = data.get(
        "routes",
        [],
    )

    if not isinstance(routes, list):
        return []

    cleaned: list[dict[str, Any]] = []

    for route in routes:

        if not isinstance(
            route,
            dict,
        ):
            continue

        try:
            source_id = int(
                route.get("source_id")
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        destinations = []

        raw_destinations = route.get(
            "destinations",
            [],
        )

        if not isinstance(
            raw_destinations,
            list,
        ):
            raw_destinations = []

        for destination in raw_destinations:

            try:

                destination_id = int(
                    destination
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

            if destination_id not in destinations:

                destinations.append(
                    destination_id
                )

        cleaned.append(
            {
                "source_id": source_id,
                "destinations": destinations,
            }
        )

    return cleaned


async def save_routes(
    routes: list[dict[str, Any]],
) -> None:

    cleaned: list[dict[str, Any]] = []

    for route in routes:

        if not isinstance(
            route,
            dict,
        ):
            continue

        try:

            source_id = int(
                route.get("source_id")
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        destinations: list[int] = []

        raw = route.get(
            "destinations",
            [],
        )

        if not isinstance(
            raw,
            list,
        ):
            raw = []

        for destination in raw:

            try:

                destination_id = int(
                    destination
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

            if destination_id not in destinations:

                destinations.append(
                    destination_id
                )

        cleaned.append(
            {
                "source_id": source_id,
                "destinations": destinations,
            }
        )

    await write_json(
        ROUTES_FILE,
        {
            "routes": cleaned
        },
    )


async def get_route(
    source_id: int,
) -> dict[str, Any] | None:

    source_id = int(source_id)

    routes = await get_routes()

    for route in routes:

        if int(
            route["source_id"]
        ) == source_id:

            return route

    return None


async def get_destinations_for_source(
    source_id: int,
) -> list[int]:

    route = await get_route(
        source_id
    )

    if not route:
        return []

    return [
        int(destination)
        for destination in route.get(
            "destinations",
            [],
        )
    ]


# ============================================================
# POSTS
# ============================================================

async def get_posts() -> list[dict[str, Any]]:

    data = await read_json(
        POSTS_FILE
    )

    posts = data.get(
        "posts",
        [],
    )

    return (
        posts
        if isinstance(posts, list)
        else []
    )


async def save_posts(
    posts: list[dict[str, Any]],
) -> None:

    await write_json(
        POSTS_FILE,
        {
            "posts": posts
        },
    )


# ============================================================
# SCHEDULE
# ============================================================

async def get_schedule() -> dict[str, Any]:

    data = await read_json(
        SCHEDULE_FILE
    )

    result = _clone(
        DEFAULTS[SCHEDULE_FILE]
    )

    result.update(data)

    return result


async def save_schedule(
    schedule: dict[str, Any],
) -> None:

    await write_json(
        SCHEDULE_FILE,
        schedule,
    )


# ============================================================
# SETTINGS
# ============================================================

async def get_settings() -> dict[str, Any]:

    data = await read_json(
        SETTINGS_FILE
    )

    result = _clone(
        DEFAULTS[SETTINGS_FILE]
    )

    result.update(data)

    return result


async def save_settings(
    settings: dict[str, Any],
) -> None:

    await write_json(
        SETTINGS_FILE,
        settings,
    )
