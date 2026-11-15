import asyncio
import time
from typing import Callable, Optional, Set

from acp.pool import AgentClientPool
from channel_cache import ChannelCache
from commands import create_default_registry
from config import default_config
from logging_config import get_logger
from mattermost_api import MattermostAPI
from message_pipeline import MessagePipeline
from sessions import SessionManager
from streaming import MattermostStreamRenderer
from user_store import UserStore

logger = get_logger(__name__)

CACHE_TTL = 60  # Update cache every 60 seconds


class MattermostBridge:
    """Thin coordinator connecting Mattermost polling to ACP sessions."""

    def __init__(
        self,
        api=None,
        config=None,
        agent_client_factory: Optional[Callable] = None,
    ):
        self.config = config or default_config
        self.api = api or MattermostAPI(config=self.config)
        self.channel_cache = ChannelCache(self.api,
                                          ttl=CACHE_TTL,
                                          debug=self.config.debug)
        self.user_store = UserStore(self.config, self.api)
        self.agent_pool = AgentClientPool(self.config,
                                          client_factory=agent_client_factory)
        self.sessions = SessionManager(self.config, self.agent_pool)
        self.renderer = MattermostStreamRenderer(self.api, self.config)
        self.commands = create_default_registry()
        self.background_tasks: Set[asyncio.Task] = set()
        self.pipeline = MessagePipeline(
            config=self.config,
            api=self.api,
            user_store=self.user_store,
            agent_pool=self.agent_pool,
            sessions=self.sessions,
            renderer=self.renderer,
            commands=self.commands,
            background_tasks=self.background_tasks,
            shutdown_callback=self.graceful_shutdown,
        )

        self.last_since = int(time.time() * 1000)
        self.bot_id: Optional[str] = None
        self.bot_username: Optional[str] = None
        self.bot_mention: Optional[str] = None
        self.is_shutting_down = False

    async def initialize(self) -> bool:
        """Initializes the bridge by connecting to Mattermost and prewarming."""
        me = await self.api.get_me()
        if not me:
            logger.error(
                "Failed to connect to Mattermost. Check your URL and TOKEN.")
            return False

        self.bot_id = me["id"]
        self.bot_username = me["username"]
        self.bot_mention = f"@{self.bot_username}"
        self.pipeline.set_bot_identity(self.bot_id, self.bot_username,
                                       self.bot_mention)
        logger.info("Connected as %s (%s)", self.bot_username, self.bot_id)

        user_mapping = self.user_store.get_user_mapping()
        if not user_mapping:
            logger.warning(
                "No user mapping found. Bridge will run as current user for "
                "all requests.")

        await self.agent_pool.prewarm(self.user_store)
        return True

    async def graceful_shutdown(self) -> None:
        """Gracefully cancels prompts, closes sessions, and drains tasks."""
        self.is_shutting_down = True
        await self.sessions.close_all()
        await self.agent_pool.terminate_all()

        current = asyncio.current_task()
        for task in list(self.background_tasks):
            if task is not current and not task.done():
                task.cancel()
        pending = [
            t for t in self.background_tasks
            if t is not current and not t.done()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _poll_channel(
        self,
        cid: str,
        channel_map: dict,
        new_since: int,
    ) -> int:
        posts_data = await self.api.get_channel_posts(cid, self.last_since)
        if not posts_data or "posts" not in posts_data:
            return new_since

        sorted_posts = sorted(
            posts_data["posts"].values(),
            key=lambda x: x["create_at"],
        )
        for post in sorted_posts:
            if self.is_shutting_down:
                break
            if post["create_at"] <= self.last_since:
                continue
            new_since = max(new_since, post["create_at"])
            await self.pipeline.process_post(post, channel_map)
        return new_since

    async def _poll_once(self) -> None:
        channel_map = await self.channel_cache.get_channel_map()
        new_since = self.last_since
        for cid in channel_map:
            if self.is_shutting_down:
                break
            new_since = await self._poll_channel(cid, channel_map, new_since)

        if not self.is_shutting_down:
            await self.sessions.prune_if_needed()
            self.last_since = new_since
            await asyncio.sleep(self.config.poll_interval)

    async def run(self) -> None:
        """Main loop for polling Mattermost and handling messages."""
        if not await self.initialize():
            return

        logger.info("Bridge is polling for messages. Press Ctrl+C to stop.")

        try:
            while not self.is_shutting_down:
                try:
                    await self._poll_once()
                except Exception as e:  # pylint: disable=broad-exception-caught
                    if self.is_shutting_down:
                        break
                    logger.error("Bridge Loop Error: %s", e)
                    await asyncio.sleep(5)
        except KeyboardInterrupt:
            pass
        finally:
            logger.info("Shutting down bridge...")
            for task in list(self.background_tasks):
                task.cancel()
            if self.background_tasks:
                await asyncio.gather(*self.background_tasks,
                                     return_exceptions=True)

            await self.agent_pool.terminate_all()
