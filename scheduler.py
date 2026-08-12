"""
Route-aware scheduler.

STRICT ROUTING:

Source A -> A destinations ONLY
Source B -> B destinations ONLY
Source C -> C destinations ONLY

There is NO fallback that sends a source to every
destination channel.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot

import storage

from config import CONFIG

from telegram_utils import (
    copy_media_group_with_retry,
    copy_message_with_retry,
)


logger = logging.getLogger("forwarder")


class Scheduler:

    def __init__(
        self,
        bot: Bot,
    ) -> None:

        self.bot = bot

        self._task: asyncio.Task | None = None

    # ============================================================
    # STATE
    # ============================================================

    def is_running(self) -> bool:

        return (
            self._task is not None
            and not self._task.done()
        )

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

        self._task = asyncio.create_task(
            self._run_loop(),
            name="forwarder-scheduler",
        )

        logger.info(
            "[INFO] Scheduler started"
        )

        return (
            True,
            "Scheduler started.",
        )

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

        self._task = None

        if task:

            task.cancel()

            try:
                await task

            except asyncio.CancelledError:
                pass

        schedule = await storage.get_schedule()

        schedule["running"] = False

        await storage.save_schedule(
            schedule
        )

        logger.info(
            "[INFO] Scheduler stopped"
        )

        return (
            True,
            "Scheduler stopped.",
        )

    async def resume_if_needed(
        self,
    ) -> None:

        schedule = await storage.get_schedule()

        if not schedule.get("running"):
            return

        if self.is_running():
            return

        logger.info(
            "[INFO] Resuming scheduler"
        )

        self._task = asyncio.create_task(
            self._run_loop(),
            name="forwarder-scheduler",
        )

    async def reset(self) -> None:

        schedule = await storage.get_schedule()

        schedule["current_index"] = 0

        schedule["next_run_iso"] = None

        schedule["last_completed_iso"] = None

        await storage.save_schedule(
            schedule
        )

    # ============================================================
    # SETTINGS
    # ============================================================

    @staticmethod
    async def settings() -> dict[str, Any]:

        settings = await storage.get_settings()

        return {
            "interval_minutes": (
                settings.get(
                    "interval_minutes"
                )
                if settings.get(
                    "interval_minutes"
                ) is not None
                else CONFIG.interval_minutes
            ),

            "source_mode": (
                settings.get(
                    "source_mode"
                )
                or CONFIG.source_mode
                or "round_robin"
            ),
        }

    # ============================================================
    # ROUTING
    # ============================================================

    @staticmethod
    async def destinations_for_source(
        source_id: int,
    ) -> list[int]:
        """
        STRICT route lookup.

        Never uses channels.json as fallback.
        """

        source_id = int(source_id)

        destinations = (
            await storage.get_destinations_for_source(
                source_id
            )
        )

        # Remove duplicates while preserving order.
        result: list[int] = []

        for destination in destinations:

            destination = int(
                destination
            )

            if destination not in result:

                result.append(
                    destination
                )

        return result

    # ============================================================
    # QUEUE
    # ============================================================

    @staticmethod
    async def build_queue() -> list[dict[str, Any]]:

        posts = await storage.get_posts()

        sources = await storage.get_sources()

        enabled_sources: set[int] = set()

        for source in sources:

            if not source.get(
                "enabled",
                True,
            ):
                continue

            try:

                source_id = int(
                    source["chat_id"]
                )

            except (
                KeyError,
                TypeError,
                ValueError,
            ):
                continue

            enabled_sources.add(
                source_id
            )

        grouped: dict[
            int,
            list[dict[str, Any]]
        ] = {}

        for post in posts:

            try:

                source_id = int(
                    post["source_chat_id"]
                )

            except (
                KeyError,
                TypeError,
                ValueError,
            ):
                continue

            # Only enabled sources.
            if source_id not in enabled_sources:
                continue

            # ------------------------------------------------
            # IMPORTANT:
            # Posts without a configured route are ignored.
            # ------------------------------------------------

            destinations = (
                await Scheduler.destinations_for_source(
                    source_id
                )
            )

            if not destinations:

                logger.warning(
                    "[WARNING] Source %s has no route; "
                    "post ignored from queue.",
                    source_id,
                )

                continue

            grouped.setdefault(
                source_id,
                [],
            ).append(post)

        # Sort every source independently.
        for source_id, source_posts in grouped.items():

            source_posts.sort(
                key=lambda post: min(
                    [
                        int(x)
                        for x in post.get(
                            "message_ids",
                            [0],
                        )
                        if str(x).lstrip("-").isdigit()
                    ]
                    or [0]
                )
            )

        settings = await Scheduler.settings()

        mode = str(
            settings.get(
                "source_mode",
                "round_robin",
            )
        ).lower()

        source_ids = sorted(
            grouped.keys()
        )

        # ====================================================
        # SEQUENTIAL
        # ====================================================

        if mode == "sequential":

            queue: list[
                dict[str, Any]
            ] = []

            for source_id in source_ids:

                queue.extend(
                    grouped[source_id]
                )

            return queue

        # ====================================================
        # ROUND ROBIN
        # ====================================================

        result: list[
            dict[str, Any]
        ] = []

        position = 0

        while True:

            added = False

            for source_id in source_ids:

                source_posts = grouped[
                    source_id
                ]

                if position < len(
                    source_posts
                ):

                    result.append(
                        source_posts[position]
                    )

                    added = True

            if not added:
                break

            position += 1

        return result

    # ============================================================
    # MAIN LOOP
    # ============================================================

    async def _run_loop(self) -> None:

        logger.info(
            "[INFO] Scheduler loop started"
        )

        try:

            while True:

                settings = (
                    await Scheduler.settings()
                )

                try:

                    minutes = float(
                        settings[
                            "interval_minutes"
                        ]
                    )

                except (
                    TypeError,
                    ValueError,
                ):

                    minutes = 10

                if minutes <= 0:
                    minutes = 1

                interval = timedelta(
                    minutes=minutes
                )

                queue = (
                    await Scheduler.build_queue()
                )

                if not queue:

                    logger.warning(
                        "[WARNING] Queue empty. "
                        "Check posts.json and routes."
                    )

                    await asyncio.sleep(
                        30
                    )

                    continue

                schedule = (
                    await storage.get_schedule()
                )

                try:

                    index = int(
                        schedule.get(
                            "current_index",
                            0,
                        )
                        or 0
                    )

                except (
                    TypeError,
                    ValueError,
                ):

                    index = 0

                index %= len(queue)

                # ------------------------------------------------
                # Next execution
                # ------------------------------------------------

                now = datetime.now(
                    timezone.utc
                )

                next_run_iso = (
                    schedule.get(
                        "next_run_iso"
                    )
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

                    except (
                        ValueError,
                        TypeError,
                    ):

                        next_run = now

                else:

                    next_run = now

                if next_run < now:

                    next_run = now

                wait = (
                    next_run - now
                ).total_seconds()

                if wait > 0:

                    logger.info(
                        "[INFO] Waiting %.1f seconds",
                        wait,
                    )

                    await asyncio.sleep(
                        wait
                    )

                # ------------------------------------------------
                # Rebuild queue before sending
                # ------------------------------------------------

                queue = (
                    await Scheduler.build_queue()
                )

                if not queue:
                    continue

                index %= len(queue)

                post = queue[index]

                await self._distribute(
                    post,
                    index + 1,
                    len(queue),
                )

                # ------------------------------------------------
                # Advance
                # ------------------------------------------------

                schedule = (
                    await storage.get_schedule()
                )

                schedule[
                    "current_index"
                ] = (
                    index + 1
                ) % len(queue)

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

                schedule[
                    "running"
                ] = True

                await storage.save_schedule(
                    schedule
                )

        except asyncio.CancelledError:

            logger.info(
                "[INFO] Scheduler cancelled"
            )

            raise

        except Exception as exc:

            logger.exception(
                "[ERROR] Scheduler crashed: %s",
                exc,
            )

            try:

                schedule = (
                    await storage.get_schedule()
                )

                schedule["running"] = False

                await storage.save_schedule(
                    schedule
                )

            except Exception:

                logger.exception(
                    "[ERROR] Could not save scheduler state"
                )

    # ============================================================
    # DISTRIBUTE
    # ============================================================

    async def _distribute(
        self,
        post: dict[str, Any],
        post_number: int,
        total: int,
    ) -> None:

        # --------------------------------------------------------
        # SOURCE
        # --------------------------------------------------------

        try:

            source_id = int(
                post["source_chat_id"]
            )

        except (
            KeyError,
            TypeError,
            ValueError,
        ):

            logger.error(
                "[ERROR] Invalid source_chat_id: %s",
                post.get(
                    "source_chat_id"
                ),
            )

            return

        # --------------------------------------------------------
        # STRICT ROUTE
        # --------------------------------------------------------

        destinations = (
            await Scheduler.destinations_for_source(
                source_id
            )
        )

        if not destinations:

            logger.error(
                "[ERROR] NO ROUTE: source=%s | post=%s",
                source_id,
                post_number,
            )

            return

        # --------------------------------------------------------
        # MESSAGE IDs
        # --------------------------------------------------------

        raw_ids = post.get(
            "message_ids",
            [],
        )

        if not raw_ids:

            single_id = post.get(
                "message_id"
            )

            if single_id is not None:

                raw_ids = [
                    single_id
                ]

        if not raw_ids:

            logger.error(
                "[ERROR] Post has no message IDs"
            )

            return

        try:

            message_ids = [
                int(x)
                for x in raw_ids
            ]

        except (
            TypeError,
            ValueError,
        ):

            logger.error(
                "[ERROR] Invalid message IDs: %s",
                raw_ids,
            )

            return

        # --------------------------------------------------------
        # MEDIA GROUP
        # --------------------------------------------------------

        post_type = str(
            post.get(
                "type",
                "",
            )
        ).lower()

        is_album = (
            post_type
            in {
                "album",
                "media_group",
                "mediagroup",
            }
            and len(message_ids) > 1
        )

        if len(message_ids) > 1:

            is_album = True

        logger.info(
            "[INFO] POST %s/%s | SOURCE %s | DESTINATIONS %s",
            post_number,
            total,
            source_id,
            destinations,
        )

        # --------------------------------------------------------
        # COPY
        # --------------------------------------------------------

        for destination_id in destinations:

            try:

                if is_album:

                    ok, error = (
                        await copy_media_group_with_retry(
                            self.bot,
                            destination_id,
                            source_id,
                            message_ids,
                        )
                    )

                else:

                    ok, error = (
                        await copy_message_with_retry(
                            self.bot,
                            destination_id,
                            source_id,
                            message_ids[0],
                        )
                    )

            except asyncio.CancelledError:

                raise

            except Exception as exc:

                ok = False
                error = str(exc)

            if ok:

                logger.info(
                    "[SUCCESS] %s -> %s | post=%s",
                    source_id,
                    destination_id,
                    post_number,
                )

            else:

                logger.error(
                    "[ERROR] %s -> %s | post=%s | %s",
                    source_id,
                    destination_id,
                    post_number,
                    error,
            )
