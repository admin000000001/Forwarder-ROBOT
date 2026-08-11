from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

SOURCES_FILE = os.path.join(DATA_DIR, "sources.json")
CHANNELS_FILE = os.path.join(DATA_DIR, "channels.json")
POSTS_FILE = os.path.join(DATA_DIR, "posts.json")
SCHEDULE_FILE = os.path.join(DATA_DIR, "schedule.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
ROUTES_FILE = os.path.join(DATA_DIR, "routes.json")

_DEFAULTS = {
    SOURCES_FILE: {"sources": []},
    CHANNELS_FILE: {"channels": []},
    POSTS_FILE: {"posts": []},
    SCHEDULE_FILE: {
        "current_index": 0,
        "running": False,
        "next_run_iso": None,
        "last_completed_iso": None,
    },
    SETTINGS_FILE: {
        "interval_minutes": 20,
        "source_mode": "sequential",
        "missed_schedule_policy": "next",
        "auto_queue_new_posts": True,
        "total_posts": None,
    },
    ROUTES_FILE: {
        "routes": []
    },
}

_LOCK = asyncio.Lock()


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _atomic_write(path: str, data: dict[str, Any]) -> None:
    _ensure_data_dir()

    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".tmp_",
        dir=directory
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)

    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _read_raw(path: str) -> dict[str, Any]:
    _ensure_data_dir()

    if not os.path.exists(path):
        default = _DEFAULTS[path]
        _atomic_write(path, default)
        return json.loads(json.dumps(default))

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()

        if not content:
            raise ValueError("empty file")

        data = json.loads(content)

        if not isinstance(data, dict):
            raise ValueError("JSON root must be object")

        return data

    except (json.JSONDecodeError, ValueError) as exc:

        print(
            f"[WARNING] {path} is corrupt: {exc}. "
            "Resetting."
        )

        try:
            backup = path + ".corrupt"

            if os.path.exists(path):
                os.replace(path, backup)

        except OSError:
            pass

        default = _DEFAULTS[path]

        _atomic_write(path, default)

        return json.loads(json.dumps(default))


async def read_json(path: str) -> dict[str, Any]:
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_uid() -> str:
    return uuid.uuid4().hex


# ============================================================
# SOURCES
# ============================================================

async def get_sources() -> list[dict[str, Any]]:
    data = await read_json(SOURCES_FILE)
    return data.get("sources", [])


async def save_sources(
    sources: list[dict[str, Any]]
) -> None:

    await write_json(
        SOURCES_FILE,
        {"sources": sources}
    )


# ============================================================
# DESTINATION CHANNELS
# ============================================================

async def get_channels() -> list[dict[str, Any]]:
    data = await read_json(CHANNELS_FILE)
    return data.get("channels", [])


async def save_channels(
    channels: list[dict[str, Any]]
) -> None:

    await write_json(
        CHANNELS_FILE,
        {"channels": channels}
    )


# ============================================================
# POSTS
# ============================================================

async def get_posts() -> list[dict[str, Any]]:
    data = await read_json(POSTS_FILE)
    return data.get("posts", [])


async def save_posts(
    posts: list[dict[str, Any]]
) -> None:

    await write_json(
        POSTS_FILE,
        {"posts": posts}
    )


# ============================================================
# SCHEDULE
# ============================================================

async def get_schedule() -> dict[str, Any]:

    data = await read_json(SCHEDULE_FILE)

    result = json.loads(
        json.dumps(
            _DEFAULTS[SCHEDULE_FILE]
        )
    )

    result.update(data)

    return result


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

    data = await read_json(SETTINGS_FILE)

    result = json.loads(
        json.dumps(
            _DEFAULTS[SETTINGS_FILE]
        )
    )

    result.update(data)

    return result


async def save_settings(
    settings: dict[str, Any]
) -> None:

    await write_json(
        SETTINGS_FILE,
        settings
    )


# ============================================================
# ROUTES
#
# Example:
#
# {
#   "routes": [
#     {
#       "source_id": -1001111111111,
#       "destinations": [
#         -1002111111111,
#         -1002111111112
#       ]
#     }
#   ]
# }
# ============================================================

async def get_routes() -> list[dict[str, Any]]:

    data = await read_json(ROUTES_FILE)

    routes = data.get("routes", [])

    if not isinstance(routes, list):
        return []

    return routes


async def save_routes(
    routes: list[dict[str, Any]]
) -> None:

    await write_json(
        ROUTES_FILE,
        {"routes": routes}
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

        if int(item.get("source_id")) == source_id:
            route = item
            break

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
        int(x) for x in destinations
    ]

    if destination_id in destinations:
        return False

    destinations.append(destination_id)

    route["destinations"] = destinations

    await save_routes(routes)

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

        if int(route.get("source_id")) != source_id:
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

        if len(new_destinations) != len(destinations):
            changed = True

        route["destinations"] = new_destinations

    routes = [
        route
        for route in routes
        if route.get("destinations")
    ]

    if changed:
        await save_routes(routes)

    return changed


async def get_destinations_for_source(
    source_id: int
) -> list[int]:

    source_id = int(source_id)

    routes = await get_routes()

    for route in routes:

        if int(route.get("source_id")) == source_id:

            return [
                int(x)
                for x in route.get(
                    "destinations",
                    []
                )
            ]

    return []


async def clear_routes() -> None:

    await save_routes([])
