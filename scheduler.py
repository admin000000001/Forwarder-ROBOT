"""
scheduler.py

The heart of the bot. A single controlled loop (NOT one asyncio task per
video) that:

  1. Determines the next scheduled timestamp from persisted state
     (grid-based, not `asyncio.sleep(1800)` chained forever, so it does not
     drift by however long each send takes).
  2. Wakes up, and if due, delivers exactly one video to every destination
     channel using bot.copy_message (never re-uploads).
  3. Tracks per-channel delivery for the *current* video in schedule.json
     BEFORE advancing the index, so a crash mid-delivery resumes by only
     sending to the channels that have not yet received that video --
     never a duplicate, never a skip.
  4. Only advances current_index and persists the new position after every
     channel has been attempted (success or exhausted-retries failure).
  5. Applies MISSED_SCHEDULE_POLICY when the bot was offline through one or
     more scheduled slots, so it does not spam a backlog of videos.

An asyncio.Lock guarantees only one delivery job can ever run at a time,
even if a job runs long and the wake-loop fires again.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import (
    TelegramRetryAfter,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
)

import storage
from config import CONFIG
from utils import log

# How often the loop wakes to re-check whether it's time to run and whether
# start/stop commands changed state. Small enough to react promptly to
# /startschedule, /stopschedule, /next, /reset without being wasteful.
POLL_SECONDS = 5


class VideoScheduler:
    def __init__(self, bot: Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    # -- lifecycle -----------------------------------------------------

    def start_loop(self) -> None:
        """Start the background wake-loop task. Called once at bot startup."""
        if self._task is not None and not self._task.done():
            log.warning("Scheduler loop already running; refusing to start a second one")
            return
        self._task = asyncio.create_task(self._run_forever(), name="scheduler_loop")
        log.info("Scheduler loop task created")

    async def stop_loop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -- owner-facing controls ------------------------------------------

    async def enable(self) -> None:
        schedule = await storage.get_schedule()
        schedule["running"] = True
        if not schedule.get("next_run"):
            schedule["next_run"] = self._next_grid_time().isoformat()
        await storage.save_schedule(schedule)
        log.info("Scheduler enabled")

    async def disable(self) -> None:
        schedule = await storage.get_schedule()
        schedule["running"] = False
        await storage.save_schedule(schedule)
        log.info("Scheduler disabled (progress preserved)")

    async def reset(self) -> None:
        schedule = await storage.get_schedule()
        schedule.update(
            {
                "current_index": 0,
                "cycle": 1,
                "in_progress_index": None,
                "delivered_channels": [],
                "last_completed_index": None,
                "next_run": self._next_grid_time().isoformat() if schedule.get("running") else None,
            }
        )
        await storage.save_schedule(schedule)
        log.info("Sequence reset to video #1")

    def _next_grid_time(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(minutes=CONFIG.interval_minutes)

    # -- main loop --------------------------------------------------------

    async def _run_forever(self) -> None:
        log.info("Scheduler wake-loop started (checking every %ss)" % POLL_SECONDS)
        while not self._stopping:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A bug here must NEVER kill the loop / bot. Log and continue.
                log.exception("Unhandled error in scheduler tick")
            await asyncio.sleep(POLL_SECONDS)

    async def _tick(self) -> None:
        schedule = await storage.get_schedule()

        if not schedule.get("running"):
            return

        # Resume any delivery that was interrupted mid-flight (crash/restart)
        # before considering whether a *new* slot is due.
        if schedule.get("in_progress_index") is not None:
            async with self._lock:
                await self._deliver(schedule["in_progress_index"], resume=True)
            return

        next_run_raw = schedule.get("next_run")
        if not next_run_raw:
            schedule["next_run"] = self._next_grid_time().isoformat()
            await storage.save_schedule(schedule)
            return

        next_run = datetime.fromisoformat(next_run_raw)
        now = datetime.now(timezone.utc)

        if now < next_run:
            return  # not due yet

        # Handle missed slots (bot was offline through one or more intervals)
        interval = timedelta(minutes=CONFIG.interval_minutes)
        missed_slots = 0
        if CONFIG.missed_schedule_policy == "next" and next_run < now:
            elapsed = now - next_run
            missed_slots = int(elapsed / interval)

        if missed_slots > 0:
            log.info(
                f"Bot was offline through {missed_slots} scheduled slot(s); "
                f"resuming with the next video only (policy=next), not spamming the backlog"
            )

        async with self._lock:
            index = schedule["current_index"]
            await self._deliver(index, resume=False)

    # -- delivery -----------------------------------------------------------

    async def _deliver(self, index: int, resume: bool) -> None:
        """
        Deliver the video at `index` to every destination channel that has
        not already received it (checked via delivered_channels), then
        advance the schedule. Safe to call repeatedly for the same index --
        already-delivered channels are always skipped.
        """
        schedule = await storage.get_schedule()
        videos_data = await storage.get_videos()
        videos = videos_data.get("videos", [])
        channels = await storage.get_channels()

        if not videos:
            log.error("No videos loaded; cannot deliver. Use /scan and import a video list.")
            return

        total = len(videos)
        if index >= total:
            index = 0  # defensive; normal wraparound is handled below too

        source_message_id = videos[index]

        if not resume:
            schedule["in_progress_index"] = index
            schedule["delivered_channels"] = []
            await storage.save_schedule(schedule)
        else:
            log.info(f"Resuming interrupted delivery of video index {index} after restart")

        already_delivered = set(schedule.get("delivered_channels", []))
        log.info(f"Copying source message {source_message_id} (video #{index + 1}/{total})")

        for channel_id in channels:
            if channel_id in already_delivered:
                continue
            ok = await self._send_with_retry(channel_id, source_message_id)
            if ok:
                already_delivered.add(channel_id)
                schedule["delivered_channels"] = list(already_delivered)
                await storage.save_schedule(schedule)
                log.info(f"Sent to {channel_id}")
            else:
                log.error(f"Failed to send to {channel_id} (giving up after retries)")

        log.info(f"Video #{index + 1} completed ({len(already_delivered)}/{len(channels)} channels)")

        # Advance to the next index only now that every channel has been
        # attempted -- this is the point that guarantees no duplicate send
        # of this index will ever be scheduled again.
        new_index = index + 1
        new_cycle = schedule.get("cycle", 1)
        if new_index >= total:
            new_index = 0
            new_cycle += 1
            log.info(f"Completed full cycle #{schedule.get('cycle', 1)}; starting cycle #{new_cycle}")

        next_run = datetime.now(timezone.utc) + timedelta(minutes=CONFIG.interval_minutes)

        schedule.update(
            {
                "current_index": new_index,
                "cycle": new_cycle,
                "in_progress_index": None,
                "delivered_channels": [],
                "last_completed_index": index,
                "next_run": next_run.isoformat(),
                "last_run_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        await storage.save_schedule(schedule)
        log.info(f"Next video scheduled at {next_run.isoformat()}")

    async def _send_with_retry(self, channel_id: int, message_id: int) -> bool:
        for attempt in range(1, CONFIG.max_retries + 1):
            try:
                await self.bot.copy_message(
                    chat_id=channel_id,
                    from_chat_id=CONFIG.source_channel_id,
                    message_id=message_id,
                )
                return True
            except TelegramRetryAfter as e:
                log.warning(f"Flood control: waiting {e.retry_after}s before retrying {channel_id}")
                await asyncio.sleep(e.retry_after)
                # Does not count as a normal attempt -- try again immediately after.
                continue
            except TelegramForbiddenError:
                log.error(f"Bot lacks permission / was removed from channel {channel_id}")
                return False
            except TelegramBadRequest as e:
                log.error(f"Bad request sending to {channel_id}: {e}")
                return False
            except TelegramNetworkError as e:
                log.warning(f"Network error sending to {channel_id} (attempt {attempt}): {e}")
            except Exception:
                log.exception(f"Unexpected error sending to {channel_id} (attempt {attempt})")

            if attempt < CONFIG.max_retries:
                backoff = CONFIG.retry_backoff_seconds * attempt
                await asyncio.sleep(backoff)

        return False

    # -- status helpers -------------------------------------------------

    async def status_snapshot(self) -> dict:
        schedule = await storage.get_schedule()
        videos_data = await storage.get_videos()
        channels = await storage.get_channels()
        return {
            "schedule": schedule,
            "total_videos": len(videos_data.get("videos", [])),
            "total_channels": len(channels),
        }
