import time
from typing import Any, Callable, Dict, List, Optional

from logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_CACHE_TTL = 60.0


class ChannelCache:
    """TTL-cached directory of Mattermost channels accessible to the bot."""

    def __init__(
        self,
        api: Any,
        ttl: float = DEFAULT_CACHE_TTL,
        clock: Callable[[], float] = time.monotonic,
        debug: bool = False,
    ):
        self.api = api
        self.ttl = ttl
        self._clock = clock
        self.debug = debug
        self._channels: List[dict] = []
        self._last_update: Optional[float] = None

    @property
    def channels(self) -> List[dict]:
        """Returns the last cached channel list."""
        return self._channels

    @property
    def last_update(self) -> Optional[float]:
        """Returns the timestamp of the last cache refresh."""
        return self._last_update

    async def get_channels(self, force: bool = False) -> List[dict]:
        """Returns cached channels, refreshing when expired or empty."""
        now = self._clock()
        expired = (self._last_update is None or
                   (now - self._last_update) > self.ttl)
        if force or not self._channels or expired:
            await self.refresh()
        return self._channels

    async def refresh(self) -> List[dict]:
        """Fetches direct and team channels from Mattermost."""
        if self.debug:
            logger.debug("Updating Mattermost channels cache...")

        channels = await self.api.get_direct_channels() or []
        teams = await self.api.get_my_teams() or []
        for team in teams:
            team_channels = await self.api.get_my_channels(team["id"]) or []
            channels.extend(team_channels)

        self._channels = list({c["id"]: c for c in channels}.values())
        self._last_update = self._clock()
        return self._channels

    async def get_channel_map(self) -> Dict[str, dict]:
        """Returns a channel_id -> channel dict mapping."""
        channels = await self.get_channels()
        return {c["id"]: c for c in channels}
