"""
scheduler.py

Route-aware Telegram post scheduler.

Source A -> Destination A1, A2
Source B -> Destination B1, B2

A post is NEVER sent to another source's destination.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import storage


logger = logging.getLogger("forwarder.scheduler")


class Scheduler:

    def __init__(
        self,
        bot: Bot,
    ) -> None:

        self.bot = bot
        self._task: asyncio.Task | None = None

        self._stop_event = asyncio.Event()

    # ========================================================
    # STATE
    # ========================================================

    def is_running(self) -> bool:

        return (
            self._task is not None
            and not self._task.done()
        )

    # ========================================================
    # START
    # ========================================================

    async def start(
        self,
    ) -> tuple[bool, str]:

        if self.is_running():

            return (
                False,
                "Scheduler is already running.",
            )

        schedule = await storage.get_schedule()

        schedule["running"] = True

        await storage.save_schedule(
            schedule
        )

        self._stop_event.clear()

        self._task = asyncio.create_task(
            self._run_loop(),
            name="forwarder-scheduler",
        )

        logger.info(
            "Scheduler started."
        )

        return (
            True,
            "Scheduler started.",
        )

    # ========================================================
    # STOP
    # ========================================================

    async def stop(
        self,
    ) -> tuple[bool, str]:

        if not self.is_running():

            schedule = await storage.get_schedule()

            schedule["running"] = False

            await storage.save_schedule(
                schedule
            )

            return (
                False,
                "Scheduler is not running.",
            )

        task = self._task

        self._stop_event.set()

        if task:

            task.cancel()

            try:

                await task

            except asyncio.CancelledError:

                pass

        self._task = None

        schedule = await storage.get_schedule()

        schedule["running"] = False

        await storage.save_schedule(
            schedule
        )

        logger.info(
            "Scheduler stopped."
        )

        return (
            True,
            "Scheduler stopped.",
        )

    # ========================================================
    # RESUME
    # ========================================================

    async def resume_if_needed(
        self,
    ) -> None:

        schedule = await storage.get_schedule()

        if not schedule.get(
            "running",
            False,
        ):

            return

        if self.is_running():
            return

        logger.info(
            "Resuming scheduler..."
        )

        self._stop_event.clear()

        self._task = asyncio.create_task(
            self._run_loop(),
            name="forwarder-scheduler",
        )

    # ========================================================
    # RESET
    # ========================================================

    async def reset(self) -> None:

        schedule = await storage.get_schedule()

        schedule["current_index"] = 0
        schedule["next_run_iso"] = None
        schedule["last_completed_iso"] = None

        await storage.save_schedule(
            schedule
        )

    # ========================================================
    # SETTINGS
    # ========================================================

    async def get_settings(
        self,
    ) -> dict[str, Any]:

        settings = await storage.get_settings()

        try:

            interval = float(
                settings.get(
                    "interval_minutes",
                    10,
                )
            )

        except Exception:

            interval = 10

        if interval <= 0:
            interval = 1

        mode = str(
            settings.get(
                "source_mode",
                "round_robin",
            )
        ).lower()

        if mode not in {
            "round_robin",
            "sequential",
        }:

            mode = "round_robin"

        return {
            "interval_minutes": interval,
            "source_mode": mode,
        }

    # ========================================================
    # ROUTES
    # ========================================================

    async def get_routes(
        self,
    ) -> list[dict[str, Any]]:

        return await storage.get_routes()

    async def get_destinations(
        self,
        source_id: int,
    ) -> list[int]:

        return await storage.get_destinations_for_source(
            source_id
        )

    # ========================================================
    # QUEUE
    # ========================================================

    async def build_queue(
        self,
    ) -> list[dict[str, Any]]:

        posts = await storage.get_posts()
        sources = await storage.get_sources()

        enabled_sources = set()

        for source in sources:

            if not source.get(
                "enabled",
                True,
            ):
                continue

            try:

                enabled_sources.add(
                    int(source["chat_id"])
                )

            except Exception:
                pass

        by_source: dict[
            int,
            list[dict[str, Any]]
        ] = {}

        for post in posts:

            try:

                source_id = int(
                    post["source_chat_id"]
                )

            except Exception:

                continue

            if source_id not in enabled_sources:
                continue

            # CRITICAL:
            # Only queue posts for sources that
            # have an actual route.
            destinations = (
                await storage.get_destinations_for_source(
                    source_id
                )
            )

            if not destinations:
                continue

            by_source.setdefault(
                source_id,
                [],
            ).append(post)

        # Sort every source independently
        for source_id in by_source:

            by_source[source_id].sort(
                key=lambda post: min(
                    [
                        int(x)
                        for x in post.get(
                            "message_ids",
                            [],
                        )
                    ]
                    or [0]
                )
            )

        settings = await self.get_settings()

        mode = settings[
            "source_mode"
        ]

        # ====================================================
        # SEQUENTIAL
        # ====================================================

        if mode == "sequential":

            result = []

            for source_id in sorted(
                by_source.keys()
            ):

                result.extend(
                    by_source[source_id]
                )

            return result

        # ====================================================
        # ROUND ROBIN
        # ====================================================

        source_ids = sorted(
            by_source.keys()
        )

        result = []

        index = 0

        while True:

            added = False

            for source_id in source_ids:

                source_queue = by_source[
                    source_id
                ]

                if index < len(
                    source_queue
                ):

                    result.append(
                        source_queue[index]
                    )

                    added = True

            if not added:
                break

            index += 1

        return result

    # ========================================================
    # WAIT
    # ========================================================

    async def _wait(
        self,
        seconds: float,
    ) -> None:

        if seconds <= 0:
            return

        try:

            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=seconds,
            )

        except asyncio.TimeoutError:

            pass

    # ========================================================
    # LOOP
    # ========================================================

    async def _run_loop(
        self,
    ) -> None:

        logger.info(
            "Scheduler loop started."
        )

        try:

            while not self._stop_event.is_set():

                settings = (
                    await self.get_settings()
                )

                interval = timedelta(
                    minutes=settings[
                        "interval_minutes"
                    ]
                )

                queue = await self.build_queue()

                if not queue:

                    logger.warning(
                        "Scheduler queue empty."
                    )

                    await self._wait(
                        min(
                            interval.total_seconds(),
                            60,
                        )
                    )

                    continue

                schedule = await storage.get_schedule()

                try:

                    current_index = int(
                        schedule.get(
                            "current_index",
                            0,
                        )
                        or 0
                    )

                except Exception:

                    current_index = 0

                current_index %= len(queue)

                # ------------------------------------------------
                # Respect next run
                # ------------------------------------------------

                next_run_iso = schedule.get(
                    "next_run_iso"
                )

                now = datetime.now(
                    timezone.utc
                )

                if next_run_iso:

                    try:

                        next_run = (
                            datetime.fromisoformat(
                                next_run_iso
                            )
                        )

                        if (
                            next_run.tzinfo
                            is None
                        ):

                            next_run = (
                                next_run.replace(
                                    tzinfo=timezone.utc
                                )
                            )

                    except Exception:

                        next_run = now

                else:

                    next_run = now

                if next_run > now:

                    wait_seconds = (
                        next_run - now
                    ).total_seconds()

                    logger.info(
                        "Next post in %.1f seconds.",
                        wait_seconds,
                    )

                    await self._wait(
                        wait_seconds
                    )

                    if self._stop_event.is_set():
                        break

                # ------------------------------------------------
                # Rebuild queue
                # ------------------------------------------------

                queue = await self.build_queue()

                if not queue:
                    continue

                current_index %= len(queue)

                post = queue[
                    current_index
                ]

                # ------------------------------------------------
                # DISTRIBUTE
                # ------------------------------------------------

                success = await self._distribute(
                    post=post,
                    position=current_index + 1,
                    total=len(queue),
                )

                # ------------------------------------------------
                # Save progress
                # ------------------------------------------------

                schedule = await storage.get_schedule()

                schedule[
                    "current_index"
                ] = (
                    current_index + 1
                ) % len(queue)

                if success:

                    schedule[
                        "last_completed_iso"
                    ] = storage.now_iso()

                schedule[
                    "next_run_iso"
                ] = (
                    datetime.now(
                        timezone.utc
                    )
                    + interval
                ).isoformat()

                await storage.save_schedule(
                    schedule
                )

        except asyncio.CancelledError:

            logger.info(
                "Scheduler loop cancelled."
            )

            raise

        except Exception as exc:

            logger.exception(
                "Scheduler crashed: %s",
                exc,
            )

            schedule = await storage.get_schedule()

            schedule["running"] = False

            await storage.save_schedule(
                schedule
            )

            self._task = None

    # ========================================================
    # DISTRIBUTE
    # ========================================================

    async def _distribute(
        self,
        post: dict[str, Any],
        position: int,
        total: int,
    ) -> bool:

        try:

            source_id = int(
                post["source_chat_id"]
            )

        except Exception:

            logger.error(
                "Post has invalid source_chat_id."
            )

            return False

        # ====================================================
        # CRITICAL ROUTE LOOKUP
        # ====================================================

        destinations = (
            await storage.get_destinations_for_source(
                source_id
            )
        )

        if not destinations:

            logger.warning(
                "No route for source %s.",
                source_id,
            )

            return False

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

        if not message_ids:

            logger.error(
                "Post has no message IDs."
            )

            return False

        message_ids = [
            int(x)
            for x in message_ids
        ]

        logger.info(
            "Distributing %s/%s | source=%s | destinations=%s",
            position,
            total,
            source_id,
            destinations,
        )

        overall_success = False

        # ====================================================
        # SEND TO ONLY THIS SOURCE'S DESTINATIONS
        # ====================================================

        for destination_id in destinations:

            try:

                if len(message_ids) > 1:

                    # Copy each album item.
                    # Telegram preserves the media content.
                    # Album grouping may not be preserved by
                    # copy_message individually.
                    for message_id in message_ids:

                        await self.bot.copy_message(
                            chat_id=destination_id,
                            from_chat_id=source_id,
                            message_id=message_id,
                        )

                        await asyncio.sleep(
                            0.15
                        )

                else:

                    await self.bot.copy_message(
                        chat_id=destination_id,
                        from_chat_id=source_id,
                        message_id=message_ids[0],
                    )

                overall_success = True

                logger.info(
                    "SUCCESS: %s -> %s",
                    source_id,
                    destination_id,
                )

            except TelegramForbiddenError as exc:

                logger.error(
                    "FORBIDDEN: %s -> %s | %s",
                    source_id,
                    destination_id,
                    exc,
                )

            except TelegramBadRequest as exc:

                logger.error(
                    "BAD REQUEST: %s -> %s | %s",
                    source_id,
                    destination_id,
                    exc,
                )

            except asyncio.CancelledError:

                raise

            except Exception as exc:

                logger.exception(
                    "COPY FAILED: %s -> %s | %s",
                    source_id,
                    destination_id,
                    exc,
                )

        return overall_success
