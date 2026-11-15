from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from acp.pool import AgentClientPool
from config import Config
from sessions import SessionManager
from user_store import ActorIdentity
from user_store import EffectiveIdentity
from user_store import UserStore


class AccessLevel(str, Enum):
    """Access control tier for bridge commands."""

    PUBLIC = "public"
    APPROVED = "approved"
    ADMIN = "admin"


class ChannelScope(str, Enum):
    """Where a command invocation may be triggered without an explicit @bot."""

    THREAD_OR_MENTION = "thread_or_mention"
    DM_OR_MENTION = "dm_or_mention"


@dataclass(frozen=True)
class CommandInvocation:
    """Parsed command invocation extracted from a Mattermost post."""

    trigger: str
    name: str
    raw_args: str
    args: Tuple[str, ...]


@dataclass(frozen=True)
class CommandResult:
    """Outcome returned by a command handler."""

    reply: Optional[str] = None


@dataclass
class CommandContext:
    """Normalized execution context passed to every command handler."""

    invocation: CommandInvocation
    post: Dict[str, Any]
    channel_id: str
    root_id: str
    is_dm: bool
    is_mentioned: bool
    actor: ActorIdentity
    effective: EffectiveIdentity
    session_key: str
    config: Config
    api: Any
    user_store: UserStore
    sessions: SessionManager
    agent_pool: AgentClientPool
    registry: Any
    bot_id: Optional[str] = None
    bot_username: Optional[str] = None
    shutdown_callback: Optional[Callable[[], Awaitable[None]]] = None

    async def reply(self, text: str) -> Optional[Dict[str, Any]]:
        """Posts a reply into the command's channel and thread."""
        return await self.api.create_post(self.channel_id,
                                          text,
                                          root_id=self.root_id)


CommandHandler = Callable[[CommandContext], Awaitable[Optional[CommandResult]]]


@dataclass(frozen=True)
class CommandSpec:
    """Declarative specification for a bridge command."""

    name: str
    handler: CommandHandler
    aliases: Tuple[str, ...] = ()
    usage: str = ""
    description: str = ""
    access: AccessLevel = AccessLevel.APPROVED
    scope: ChannelScope = ChannelScope.THREAD_OR_MENTION
    resolves_impersonation: bool = True
