"""
scheduler.py

Owns the single distribution loop:
  - Builds a deterministic combined post queue from all enabled sources
    (round_robin or sequential).
  - Waits for the next scheduled time.
  - Copies the due post to every destination channel (failure-isolated).
  - Persists progress only after the post has been processed.
  - Wraps back to post #1 after the last post.
  - Guarantees at most ONE active scheduler loop at a time.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot

import storage
from config import CONFIG
from telegram_utils import copy_media_group_with_retry, copy_message_with_retry

logger = logging.getLogger("forwarder")


class Scheduler:
    """Holds the single scheduler task instance for the whole process."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._task: asyncio.Task | None = None

    # -- public API -----------------------------------------------------

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> tuple[bool, str]:
        # Duplicate-protection: never allow two concurrent scheduler loops.
        if self.is_running():
            return False, "Scheduler is already running."
        schedule = await storage.get_schedule()
        schedule["running"] = True
        await storage.save_schedule(schedule)
        self._task = asyncio.create_task(self._run_loop())
        return True, "Scheduler started."

    async def stop(self) -> tuple[bool, str]:
        if not self.is_running():
            schedule = await storage.get_schedule()
            schedule["running"] = False
            await storage.save_schedule(schedule)
            return False, "Scheduler is not running."
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        schedule = await storage.get_schedule()
        schedule["running"] = False
        await storage.save_schedule(schedule)
        return True, "Scheduler stopped."

    async def resume_if_needed(self) -> None:
        """Called at startup: resumes the scheduler if it was left running."""
        schedule = await storage.get_schedule()
        if schedule.get("running"):
            logger.info("[INFO] Resuming scheduler after restart")
            self._task = asyncio.create_task(self._run_loop())

    async def reset(self) -> None:
        schedule = await storage.get_schedule()
        schedule["current_index"] = 0
        schedule["next_run_iso"] = None
        schedule["last_completed_iso"] = None
        await storage.save_schedule(schedule)

    # -- queue building ---------------------------------------------------

    @staticmethod
    async def get_effective_settings() -> dict[str, Any]:
        settings = await storage.get_settings()
        return {
            "interval_minutes": settings.get("interval_minutes") or CONFIG.interval_minutes,
            "source_mode": settings.get("source_mode") or CONFIG.source_mode,
            "missed_schedule_policy": settings.get("missed_schedule_policy")
            or CONFIG.missed_schedule_policy,
            "total_posts": settings.get("total_posts") or CONFIG.total_posts,
        }

    @staticmethod
    async def build_queue() -> list[dict[str, Any]]:
        """
        Builds the combined, deterministic post queue across all enabled
        sources according to the configured source mode.
        """
        posts = await storage.get_posts()
        sources = await storage.get_sources()
        enabled_source_ids = {s["chat_id"] for s in sources if s.get("enabled", True)}

        by_source: dict[int, list[dict[str, Any]]] = {}
        for post in posts:
            sid = post.get("source_chat_id")
            if sid not in enabled_source_ids:
                continue
            by_source.setdefault(sid, []).append(post)

        settings = await Scheduler.get_effective_settings()
        mode = settings["source_mode"]

        if mode == "sequential":
            queue: list[dict[str, Any]] = []
            for sid in by_source:
                queue.extend(by_source[sid])
            return queue

        # round_robin (default)
        queues = list(by_source.values())
        combined: list[dict[str, Any]] = []
        i = 0
        while any(i < len(q) for q in queues):
            for q in queues:
                if i < len(q):
                    combined.append(q[i])
            i += 1
        return combined

    # -- core loop ----------------------------------------------------------

    async def _run_loop(self) -> None:
        logger.info("[INFO] Scheduler loop starting")
        try:
            while True:
                settings = await Scheduler.get_effective_settings()
                interval = timedelta(minutes=settings["interval_minutes"])

                queue = await self.build_queue()
                if not queue:
                    logger.warning("[WARNING] No posts available; scheduler idling")
                    await asyncio.sleep(min(interval.total_seconds(), 60))
                    continue

                schedule = await storage.get_schedule()
                index = schedule.get("current_index", 0) % len(queue)

                next_run_iso = schedule.get("next_run_iso")
                now = datetime.now(timezone.utc)
                if next_run_iso:
                    try:
                        next_run = datetime.fromisoformat(next_run_iso)
                    except ValueError:
                        next_run = now
                else:
                    next_run = now

                # MISSED_SCHEDULE_POLICY=next: if we're behind schedule (e.g.
                # after downtime), do not burst-send everything that was
                # missed -- just wait for/execute the *next* single post.
                if next_run < now:
                    next_run = now

                wait_seconds = (next_run - now).total_seconds()
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)

                queue = await self.build_queue()
                if not queue:
                    continue
                index = index % len(queue)
                post = queue[index]

                await self._distribute(post, index + 1, len(queue))

                schedule = await storage.get_schedule()
                new_index = (index + 1) % len(queue)
                schedule["current_index"] = new_index
                schedule["last_completed_iso"] = storage.now_iso()
                schedule["next_run_iso"] = (
                    datetime.now(timezone.utc) + interval
                ).isoformat()
                await storage.save_schedule(schedule)
        except asyncio.CancelledError:
            logger.info("[INFO] Scheduler loop cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - the loop must never die silently
            logger.error("[ERROR] Scheduler loop crashed: %s", exc)
            schedule = await storage.get_schedule()
            schedule["running"] = False
            await storage.save_schedule(schedule)

    async def _distribute(self, post: dict[str, Any], post_number: int, total: int) -> None:
        channels = await storage.get_channels()
        enabled_channels = [c for c in channels if c.get("enabled", True)]
        if not enabled_channels:
            logger.warning("[WARNING] No destination channels configured; skipping post %s", post_number)
            return

        source_chat_id = post["source_chat_id"]
        message_ids = post["message_ids"]
        is_album = post.get("type") == "album" and len(message_ids) > 1

        for channel in enabled_channels:
            dest_id = channel["chat_id"]
            try:
                if is_album:
                    ok, err = await copy_media_group_with_retry(
                        self.bot, dest_id, source_chat_id, message_ids
                    )
                else:
                    ok, err = await copy_message_with_retry(
                        self.bot, dest_id, source_chat_id, message_ids[0]
                    )
            except Exception as exc:  # noqa: BLE001 - destination isolation
                ok, err = False, str(exc)

            if ok:
                logger.info(
                    "[SUCCESS] Post %s/%s copied to %s", post_number, total, dest_id
                )
            else:
                logger.error(
                    "[ERROR] Failed to copy post %s to channel %s: %s",
                    post_number,
                    dest_id,
                    err,
                )
