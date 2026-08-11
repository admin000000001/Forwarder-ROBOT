"""
scheduler.py

Route-aware Telegram post scheduler.

Routing model:

    Source A -> Destination A1, A2, A3
    Source B -> Destination B1, B2, B3
    Source C -> Destination C1, C2, C3

Each post is distributed ONLY to destinations belonging
to its own source route.

Supported post types:
    - single messages
    - videos
    - photos
    - documents
    - audio
    - animations
    - text/caption posts
    - media albums/groups

The actual Telegram copy operations are handled by telegram_utils.py.
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
    """
    Single scheduler instance.

    Important:
        Only one scheduler loop can run at a time.
    """

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._task: asyncio.Task | None = None

    # ============================================================
    # BASIC STATE
    # ============================================================

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> tuple[bool, str]:
        """
        Start scheduler.
        """

        if self.is_running():
            return False, "Scheduler is already running."

        schedule = await storage.get_schedule()

        schedule["running"] = True

        await storage.save_schedule(schedule)

        self._task = asyncio.create_task(
            self._run_loop(),
            name="forwarder-scheduler",
        )

        logger.info("[INFO] Scheduler started")

        return True, "Scheduler started."

    async def stop(self) -> tuple[bool, str]:
        """
        Stop scheduler safely.
        """

        if not self.is_running():

            schedule = await storage.get_schedule()

            schedule["running"] = False

            await storage.save_schedule(schedule)

            return False, "Scheduler is not running."

        task = self._task

        if task is not None:
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

        self._task = None

        schedule = await storage.get_schedule()

        schedule["running"] = False

        await storage.save_schedule(schedule)

        logger.info("[INFO] Scheduler stopped")

        return True, "Scheduler stopped."

    async def resume_if_needed(self) -> None:
        """
        Resume scheduler after restart if it was running previously.
        """

        schedule = await storage.get_schedule()

        if schedule.get("running"):

            if self.is_running():
                return

            logger.info("[INFO] Resuming scheduler after restart")

            self._task = asyncio.create_task(
                self._run_loop(),
                name="forwarder-scheduler",
            )

    async def reset(self) -> None:
        """
        Reset scheduler position.
        """

        schedule = await storage.get_schedule()

        schedule["current_index"] = 0
        schedule["next_run_iso"] = None
        schedule["last_completed_iso"] = None

        await storage.save_schedule(schedule)

        logger.info("[INFO] Scheduler sequence reset")

    # ============================================================
    # SETTINGS
    # ============================================================

    @staticmethod
    async def get_effective_settings() -> dict[str, Any]:
        """
        Merge persistent settings with CONFIG defaults.
        """

        settings = await storage.get_settings()

        return {
            "interval_minutes": (
                settings.get("interval_minutes")
                if settings.get("interval_minutes") is not None
                else CONFIG.interval_minutes
            ),

            "source_mode": (
                settings.get("source_mode")
                if settings.get("source_mode")
                else CONFIG.source_mode
            ),

            "missed_schedule_policy": (
                settings.get("missed_schedule_policy")
                if settings.get("missed_schedule_policy")
                else CONFIG.missed_schedule_policy
            ),

            "total_posts": (
                settings.get("total_posts")
                if settings.get("total_posts") is not None
                else CONFIG.total_posts
            ),
        }

    # ============================================================
    # ROUTE HELPERS
    # ============================================================

    @staticmethod
    async def get_routes() -> list[dict[str, Any]]:
        """
        Return configured source -> destination routes.

        Expected storage format:

        {
            "routes": [
                {
                    "source_id": -1001111111111,
                    "destinations": [
                        -1002111111111,
                        -1002111111112
                    ]
                }
            ]
        }

        If the newer route storage helper exists, use it.
        Otherwise construct routes from sources/channels.
        """

        # --------------------------------------------------------
        # Preferred route storage
        # --------------------------------------------------------

        if hasattr(storage, "get_routes"):

            try:
                routes = await storage.get_routes()

                if isinstance(routes, list):
                    return routes

            except Exception as exc:

                logger.warning(
                    "[WARNING] Could not load routes: %s",
                    exc,
                )

        # --------------------------------------------------------
        # Compatibility fallback
        # --------------------------------------------------------
        #
        # This fallback supports older projects where:
        #
        # sources.json
        # channels.json
        #
        # are still used independently.
        #
        # In that case all enabled destinations are treated as
        # destinations for all enabled sources.
        #
        # For TRUE source-specific routing, add route storage.
        # --------------------------------------------------------

        sources = await storage.get_sources()
        channels = await storage.get_channels()

        enabled_sources = [
            s for s in sources
            if s.get("enabled", True)
        ]

        enabled_destinations = [
            c for c in channels
            if c.get("enabled", True)
        ]

        routes: list[dict[str, Any]] = []

        for source in enabled_sources:

            source_id = source.get("chat_id")

            if source_id is None:
                continue

            destinations = []

            for channel in enabled_destinations:

                dest_id = channel.get("chat_id")

                if dest_id is not None:
                    destinations.append(dest_id)

            routes.append(
                {
                    "source_id": source_id,
                    "destinations": destinations,
                }
            )

        return routes

    @staticmethod
    async def get_route_for_source(
        source_id: int,
    ) -> dict[str, Any] | None:
        """
        Find the route belonging to a source.
        """

        routes = await Scheduler.get_routes()

        for route in routes:

            try:
                route_source = int(route.get("source_id"))

            except (TypeError, ValueError):
                continue

            if route_source == int(source_id):
                return route

        return None

    @staticmethod
    async def get_destinations_for_source(
        source_id: int,
    ) -> list[int]:
        """
        Return ONLY destinations assigned to this source.
        """

        route = await Scheduler.get_route_for_source(source_id)

        if not route:
            return []

        result: list[int] = []

        for destination in route.get("destinations", []):

            try:
                result.append(int(destination))

            except (TypeError, ValueError):
                continue

        return result

    # ============================================================
    # QUEUE BUILDING
    # ============================================================

    @staticmethod
    async def build_queue() -> list[dict[str, Any]]:
        """
        Build scheduler queue from all enabled sources.

        round_robin:

            A1
            B1
            C1
            A2
            B2
            C2

        sequential:

            A1
            A2
            A3
            B1
            B2
            B3
            C1
            C2
            C3

        IMPORTANT:

        The queue contains posts only.

        Destination selection happens later using the post's
        source_chat_id.
        """

        posts = await storage.get_posts()

        sources = await storage.get_sources()

        enabled_source_ids = set()

        for source in sources:

            if not source.get("enabled", True):
                continue

            source_id = source.get("chat_id")

            if source_id is None:
                continue

            try:
                enabled_source_ids.add(int(source_id))

            except (TypeError, ValueError):
                continue

        # --------------------------------------------------------
        # Group posts by source
        # --------------------------------------------------------

        by_source: dict[int, list[dict[str, Any]]] = {}

        for post in posts:

            source_id = post.get("source_chat_id")

            if source_id is None:
                continue

            try:
                source_id = int(source_id)

            except (TypeError, ValueError):
                continue

            if source_id not in enabled_source_ids:
                continue

            by_source.setdefault(
                source_id,
                [],
            ).append(post)

        # --------------------------------------------------------
        # Sort each source by message ID
        # --------------------------------------------------------

        for source_id in by_source:

            by_source[source_id].sort(
                key=lambda item: (
                    min(
                        item.get("message_ids", [0])
                        or [0]
                    )
                )
            )

        settings = await Scheduler.get_effective_settings()

        mode = str(
            settings.get("source_mode")
            or "round_robin"
        ).lower()

        # ========================================================
        # SEQUENTIAL
        # ========================================================

        if mode == "sequential":

            queue: list[dict[str, Any]] = []

            for source_id in by_source:

                queue.extend(
                    by_source[source_id]
                )

            return queue

        # ========================================================
        # ROUND ROBIN
        # ========================================================

        queues = list(
            by_source.values()
        )

        combined: list[dict[str, Any]] = []

        index = 0

        while any(
            index < len(queue)
            for queue in queues
        ):

            for queue in queues:

                if index < len(queue):

                    combined.append(
                        queue[index]
                    )

            index += 1

        return combined

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
                    await Scheduler.get_effective_settings()
                )

                try:

                    interval_minutes = float(
                        settings["interval_minutes"]
                    )

                except (
                    TypeError,
                    ValueError,
                ):

                    interval_minutes = 10

                if interval_minutes <= 0:
                    interval_minutes = 1

                interval = timedelta(
                    minutes=interval_minutes
                )

                # ------------------------------------------------
                # Build queue
                # ------------------------------------------------

                queue = await Scheduler.build_queue()

                if not queue:

                    logger.warning(
                        "[WARNING] No posts available; scheduler idling"
                    )

                    await asyncio.sleep(
                        min(
                            interval.total_seconds(),
                            60,
                        )
                    )

                    continue

                # ------------------------------------------------
                # Current scheduler state
                # ------------------------------------------------

                schedule = await storage.get_schedule()

                current_index = int(
                    schedule.get(
                        "current_index",
                        0,
                    )
                    or 0
                )

                current_index %= len(queue)

                # ------------------------------------------------
                # Calculate next execution time
                # ------------------------------------------------

                next_run_iso = schedule.get(
                    "next_run_iso"
                )

                now = datetime.now(
                    timezone.utc
                )

                if next_run_iso:

                    try:

                        next_run = datetime.fromisoformat(
                            next_run_iso
                        )

                        if next_run.tzinfo is None:

                            next_run = next_run.replace(
                                tzinfo=timezone.utc
                            )

                    except (
                        ValueError,
                        TypeError,
                    ):

                        next_run = now

                else:

                    next_run = now

                # Do not burst-send missed posts.

                if next_run < now:

                    next_run = now

                wait_seconds = (
                    next_run - now
                ).total_seconds()

                if wait_seconds > 0:

                    logger.info(
                        "[INFO] Next post in %.1f seconds",
                        wait_seconds,
                    )

                    await asyncio.sleep(
                        wait_seconds
                    )

                # ------------------------------------------------
                # Rebuild queue after waiting
                # ------------------------------------------------

                queue = await Scheduler.build_queue()

                if not queue:
                    continue

                current_index %= len(queue)

                post = queue[
                    current_index
                ]

                # ------------------------------------------------
                # Distribute
                # ------------------------------------------------

                await self._distribute(
                    post=post,
                    post_number=current_index + 1,
                    total=len(queue),
                )

                # ------------------------------------------------
                # Save progress
                # ------------------------------------------------

                schedule = await storage.get_schedule()

                new_index = (
                    current_index + 1
                ) % len(queue)

                schedule[
                    "current_index"
                ] = new_index

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
                "[INFO] Scheduler loop cancelled"
            )

            raise

        except Exception as exc:

            logger.exception(
                "[ERROR] Scheduler loop crashed: %s",
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
    # DISTRIBUTION
    # ============================================================

    async def _distribute(
        self,
        post: dict[str, Any],
        post_number: int,
        total: int,
    ) -> None:

        source_chat_id = post.get(
            "source_chat_id"
        )

        if source_chat_id is None:

            logger.error(
                "[ERROR] Post has no source_chat_id"
            )

            return

        try:

            source_chat_id = int(
                source_chat_id
            )

        except (
            TypeError,
            ValueError,
        ):

            logger.error(
                "[ERROR] Invalid source_chat_id: %s",
                source_chat_id,
            )

            return

        # --------------------------------------------------------
        # CRITICAL:
        # Get destinations ONLY for this source.
        # --------------------------------------------------------

        destinations = (
            await Scheduler.get_destinations_for_source(
                source_chat_id
            )
        )

        if not destinations:

            logger.warning(
                "[WARNING] No destinations configured for source %s; "
                "post %s skipped",
                source_chat_id,
                post_number,
            )

            return

        # --------------------------------------------------------
        # Extract message IDs
        # --------------------------------------------------------

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
                "[ERROR] Post %s has no message IDs",
                post_number,
            )

            return

        try:

            message_ids = [
                int(mid)
                for mid in message_ids
            ]

        except (
            TypeError,
            ValueError,
        ):

            logger.error(
                "[ERROR] Invalid message IDs in post %s",
                post_number,
            )

            return

        # --------------------------------------------------------
        # Determine album
        # --------------------------------------------------------

        post_type = str(
            post.get("type", "")
        ).lower()

        is_album = (
            post_type in {
                "album",
                "media_group",
                "mediagroup",
            }
            and len(message_ids) > 1
        )

        # Some imported posts may not contain type.
        # If multiple IDs are present, treat it as a media group.
        if len(message_ids) > 1:

            is_album = True

        # --------------------------------------------------------
        # Send to each destination
        # --------------------------------------------------------

        logger.info(
            "[INFO] Distributing post %s/%s | "
            "source=%s | destinations=%s",
            post_number,
            total,
            source_chat_id,
            len(destinations),
        )

        for destination_id in destinations:

            try:

                if is_album:

                    ok, error = (
                        await copy_media_group_with_retry(
                            self.bot,
                            destination_id,
                            source_chat_id,
                            message_ids,
                        )
                    )

                else:

                    ok, error = (
                        await copy_message_with_retry(
                            self.bot,
                            destination_id,
                            source_chat_id,
                            message_ids[0],
                        )
                    )

            except asyncio.CancelledError:

                raise

            except Exception as exc:

                ok = False
                error = str(exc)

            # ----------------------------------------------------
            # Logging
            # ----------------------------------------------------

            if ok:

                logger.info(
                    "[SUCCESS] Post %s/%s copied: "
                    "%s -> %s",
                    post_number,
                    total,
                    source_chat_id,
                    destination_id,
                )

            else:

                logger.error(
                    "[ERROR] Failed to copy post %s/%s: "
                    "%s -> %s | %s",
                    post_number,
                    total,
                    source_chat_id,
                    destination_id,
                    error,
              )
