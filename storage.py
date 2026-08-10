"""
storage.py

All persistent JSON state lives here:
  - videos.json    : the ordered list of source video message IDs
  - channels.json  : destination channel IDs
  - schedule.json  : scheduler position, timing, and in-progress delivery
                      tracking (used to prevent duplicate sends on restart)

Every read/write goes through utils.read_json / utils.write_json, which are
atomic and lock-protected, so concurrent access from commands + the
scheduler loop can never corrupt state or race.
"""

from __future__ import annotations

from typing import Any

from config import CONFIG, VIDEOS_FILE, CHANNELS_FILE, SCHEDULE_FILE
from utils import read_json, write_json, log

DEFAULT_VIDEOS: dict[str, Any] = {
    "source_channel_id": CONFIG.source_channel_id,
    "videos": [],
}

DEFAULT_CHANNELS: dict[str, Any] = {
    "channels": [],
}

DEFAULT_SCHEDULE: dict[str, Any] = {
    "current_index": 0,       # 0-based index into videos.json["videos"]
    "cycle": 1,                # how many full 1440-video loops completed
    "next_run": None,          # ISO 8601 timestamp of the next scheduled send
    "running": False,          # whether the scheduler should be active
    "in_progress_index": None,  # index currently being delivered (crash marker)
    "delivered_channels": [],   # channels already delivered for in_progress_index
    "last_completed_index": None,
    "last_run_at": None,
}


# ---------------------------------------------------------------------------
# videos.json
# ---------------------------------------------------------------------------

async def get_videos() -> dict[str, Any]:
    data = await read_json(VIDEOS_FILE, DEFAULT_VIDEOS)
    if "videos" not in data or not isinstance(data["videos"], list):
        log.warning("videos.json malformed; resetting to empty list")
        data = dict(DEFAULT_VIDEOS)
    return data


async def save_videos(data: dict[str, Any]) -> None:
    await write_json(VIDEOS_FILE, data)


async def add_video_id(message_id: int) -> bool:
    """Append a message_id if not already present. Returns True if added."""
    data = await get_videos()
    if message_id in data["videos"]:
        return False
    data["videos"].append(message_id)
    await save_videos(data)
    return True


async def import_video_ids(ids: list[int]) -> int:
    """Replace/merge the video ID list from an owner-provided import."""
    data = await get_videos()
    existing = set(data["videos"])
    added = 0
    for vid in ids:
        if not isinstance(vid, int):
            continue
        if vid not in existing:
            data["videos"].append(vid)
            existing.add(vid)
            added += 1
    await save_videos(data)
    return added


# ---------------------------------------------------------------------------
# channels.json
# ---------------------------------------------------------------------------

async def get_channels() -> list[int]:
    data = await read_json(CHANNELS_FILE, DEFAULT_CHANNELS)
    channels = data.get("channels", [])
    if not isinstance(channels, list):
        log.warning("channels.json malformed; resetting to empty list")
        return []
    return channels


async def add_channel(channel_id: int) -> bool:
    channels = await get_channels()
    if channel_id in channels:
        return False
    channels.append(channel_id)
    await write_json(CHANNELS_FILE, {"channels": channels})
    return True


async def remove_channel(channel_id: int) -> bool:
    channels = await get_channels()
    if channel_id not in channels:
        return False
    channels.remove(channel_id)
    await write_json(CHANNELS_FILE, {"channels": channels})
    return True


# ---------------------------------------------------------------------------
# schedule.json
# ---------------------------------------------------------------------------

async def get_schedule() -> dict[str, Any]:
    data = await read_json(SCHEDULE_FILE, DEFAULT_SCHEDULE)
    merged = dict(DEFAULT_SCHEDULE)
    merged.update(data)
    return merged


async def save_schedule(data: dict[str, Any]) -> None:
    await write_json(SCHEDULE_FILE, data)
