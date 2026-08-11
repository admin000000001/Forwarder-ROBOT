"""
storage.py

JSON-file persistence layer. No external databases are used.
All writes are atomic (write to temp file, flush, fsync, then os.replace)
so a crash mid-write never corrupts the on-disk JSON.
"""

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

_DEFAULTS: dict[str, dict[str, Any]] = {
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
        "interval_minutes": None,  # None => fall back to CONFIG default
        "source_mode": None,
        "missed_schedule_policy": None,
        "auto_queue_new_posts": None,
        "total_posts": None,
    },
}

# A single global lock per process is sufficient here: the bot is a single
# asyncio process and JSON files are small, so serializing writes avoids
# any read/modify/write race between concurrent handlers.
_LOCK = asyncio.Lock()


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _atomic_write(path: str, data: dict[str, Any]) -> None:
    _ensure_data_dir()
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
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
            raise ValueError("root JSON element must be an object")
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        # Malformed JSON must never crash the bot. Reset to a safe default
        # and keep a .corrupt backup for manual inspection.
        print(f"[WARNING] {path} is corrupt ({exc}); resetting to default.")
        try:
            backup_path = path + ".corrupt"
            if os.path.exists(path):
                os.replace(path, backup_path)
        except OSError:
            pass
        default = _DEFAULTS[path]
        _atomic_write(path, default)
        return json.loads(json.dumps(default))


async def read_json(path: str) -> dict[str, Any]:
    async with _LOCK:
        return await asyncio.to_thread(_read_raw, path)


async def write_json(path: str, data: dict[str, Any]) -> None:
    async with _LOCK:
        await asyncio.to_thread(_atomic_write, path, data)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_uid() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Typed convenience helpers
# ---------------------------------------------------------------------------


async def get_sources() -> list[dict[str, Any]]:
    data = await read_json(SOURCES_FILE)
    return data.get("sources", [])


async def save_sources(sources: list[dict[str, Any]]) -> None:
    await write_json(SOURCES_FILE, {"sources": sources})


async def get_channels() -> list[dict[str, Any]]:
    data = await read_json(CHANNELS_FILE)
    return data.get("channels", [])


async def save_channels(channels: list[dict[str, Any]]) -> None:
    await write_json(CHANNELS_FILE, {"channels": channels})


async def get_posts() -> list[dict[str, Any]]:
    data = await read_json(POSTS_FILE)
    return data.get("posts", [])


async def save_posts(posts: list[dict[str, Any]]) -> None:
    await write_json(POSTS_FILE, {"posts": posts})


async def get_schedule() -> dict[str, Any]:
    data = await read_json(SCHEDULE_FILE)
    merged = json.loads(json.dumps(_DEFAULTS[SCHEDULE_FILE]))
    merged.update(data)
    return merged


async def save_schedule(schedule: dict[str, Any]) -> None:
    await write_json(SCHEDULE_FILE, schedule)


async def get_settings() -> dict[str, Any]:
    data = await read_json(SETTINGS_FILE)
    merged = json.loads(json.dumps(_DEFAULTS[SETTINGS_FILE]))
    merged.update(data)
    return merged


async def save_settings(settings: dict[str, Any]) -> None:
    await write_json(SETTINGS_FILE, settings)
