"""
utils.py

Shared utilities:
 - logging setup (never logs secrets)
 - atomic, corruption-safe JSON read/write helpers with automatic .bak backups
 - a simple async-safe per-file lock registry so concurrent writers never race
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("video_bot")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger  # avoid duplicate handlers on reload

    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        file_handler = logging.FileHandler("bot.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        # If the filesystem is read-only (some hosts), just log to stdout.
        pass

    return logger


log = setup_logging()

_file_locks: dict[str, asyncio.Lock] = {}


def _lock_for(path: Path) -> asyncio.Lock:
    key = str(path)
    if key not in _file_locks:
        _file_locks[key] = asyncio.Lock()
    return _file_locks[key]


def read_json_sync(path: Path, default: Any) -> Any:
    """
    Synchronous, corruption-safe JSON read. Used only at startup before the
    event loop / locks matter. Falls back to the .bak file, then to `default`.
    """
    if not path.exists():
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"Corrupted JSON at {path.name} ({e}); attempting backup recovery")
        bak = path.with_suffix(path.suffix + ".bak")
        if bak.exists():
            try:
                with open(bak, "r", encoding="utf-8") as f:
                    data = json.load(f)
                log.info(f"Recovered {path.name} from backup")
                return data
            except (json.JSONDecodeError, OSError):
                log.error(f"Backup for {path.name} is also corrupted; using default")
        return default


async def read_json(path: Path, default: Any) -> Any:
    lock = _lock_for(path)
    async with lock:
        return await asyncio.to_thread(read_json_sync, path, default)


def _write_json_sync(path: Path, data: Any) -> None:
    """
    Atomic write: write to a temp file in the same directory, fsync it,
    back up the previous version, then atomically replace the target.
    This guarantees the current index is never lost mid-write, even if the
    process is killed at any point during this function.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=path.stem + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        if path.exists():
            bak_path = path.with_suffix(path.suffix + ".bak")
            try:
                shutil.copy2(path, bak_path)
            except OSError as e:
                log.warning(f"Could not create backup for {path.name}: {e}")

        os.replace(tmp_path, path)  # atomic on POSIX and Windows
    except Exception:
        # Clean up the temp file if something went wrong before replace().
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


async def write_json(path: Path, data: Any) -> None:
    lock = _lock_for(path)
    async with lock:
        await asyncio.to_thread(_write_json_sync, path, data)
