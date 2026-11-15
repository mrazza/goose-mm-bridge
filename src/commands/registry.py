from typing import Any, Awaitable, Callable, Dict, List, Optional

from acp.pool import AgentClientPool
from commands.models import AccessLevel
from commands.models import ChannelScope
from commands.models import CommandContext
from commands.models import CommandInvocation
from commands.models import CommandResult
from commands.models import CommandSpec
from config import Config
from logging_config import get_logger
from sessions import SessionManager
from user_store import UserStore
from utils import clean_message
from utils import get_session_key

logger = get_logger(__name__)


async def handle_help(ctx: CommandContext) -> CommandResult:
    """Generates permission-aware command help from registered specs."""
    standard_specs = [
        spec for spec in ctx.registry.specs if spec.access != AccessLevel.ADMIN
    ]
    admin_specs = [
        spec for spec in ctx.registry.specs if spec.access == AccessLevel.ADMIN
    ]

    lines = ["📖 **Available Commands**"]
    for spec in standard_specs:
        usage = spec.usage or f"!{spec.name}"
        alias_str = ""
        if spec.aliases:
            formatted = ", ".join(f"`!{a}`" for a in spec.aliases)
            alias_str = f" (aliases: {formatted})"
        lines.append(f"- `{usage}`{alias_str} — {spec.description}")

    if ctx.actor.is_admin and admin_specs:
        lines.append("")
        lines.append("**Admin Commands**")
        for spec in admin_specs:
            usage = spec.usage or f"!{spec.name}"
            alias_str = ""
            if spec.aliases:
                formatted = ", ".join(f"`!{a}`" for a in spec.aliases)
                alias_str = f" (aliases: {formatted})"
            lines.append(f"- `{usage}`{alias_str} — {spec.description}")

    lines.append("")
    lines.append(
        "*Tip: Commands work directly in DMs and active threads; mention "
        "the bot in channel posts (e.g. `@bot !help`).*")
    return CommandResult("\n".join(lines))


class CommandRegistry:
    """Parses, authorizes, and dispatches registered !commands."""

    def __init__(self):
        self._specs: List[CommandSpec] = []
        self._triggers: Dict[str, CommandSpec] = {}

    @property
    def specs(self) -> List[CommandSpec]:
        """Returns all registered command specs in registration order."""
        return list(self._specs)

    def register(self, spec: CommandSpec) -> None:
        """Registers a command and its aliases."""
        self._specs.append(spec)
        self._triggers[f"!{spec.name.lower()}"] = spec
        for alias in spec.aliases:
            self._triggers[f"!{alias.lower()}"] = spec

    def parse(
        self,
        raw_message: str,
        bot_mention: Optional[str],
    ) -> Optional[CommandInvocation]:
        """Parses a raw Mattermost message into a CommandInvocation if valid."""
        cleaned = clean_message(raw_message, bot_mention).strip()
        if not cleaned or not cleaned.startswith("!"):
            return None

        tokens = cleaned.split(maxsplit=1)
        trigger = tokens[0].lower()
        spec = self._triggers.get(trigger)
        if spec is None:
            return None

        raw_args = tokens[1].strip() if len(tokens) > 1 else ""
        args = tuple(raw_args.split()) if raw_args else ()
        return CommandInvocation(
            trigger=trigger,
            name=spec.name,
            raw_args=raw_args,
            args=args,
        )

    def _in_scope(
        self,
        spec: CommandSpec,
        *,
        is_dm: bool,
        is_mentioned: bool,
        root_id: str,
        sessions: SessionManager,
    ) -> bool:
        if spec.scope == ChannelScope.DM_OR_MENTION:
            return is_dm or is_mentioned
        return is_dm or is_mentioned or sessions.has_thread(root_id)

    async def dispatch(
        self,
        invocation: CommandInvocation,
        *,
        post: Dict[str, Any],
        is_dm: bool,
        is_mentioned: bool,
        root_id: str,
        config: Config,
        api: Any,
        user_store: UserStore,
        sessions: SessionManager,
        agent_pool: AgentClientPool,
        bot_id: Optional[str] = None,
        bot_username: Optional[str] = None,
        shutdown_callback: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> bool:
        """Validates scope/permissions and executes the command handler."""
        spec = self._triggers.get(invocation.trigger)
        if spec is None:
            return False

        if not self._in_scope(
                spec,
                is_dm=is_dm,
                is_mentioned=is_mentioned,
                root_id=root_id,
                sessions=sessions,
        ):
            return False

        sender_id = post["user_id"]
        channel_id = post["channel_id"]
        actor, effective = await user_store.resolve_identities(
            sender_id,
            resolve_impersonation=spec.resolves_impersonation,
        )

        if spec.access == AccessLevel.APPROVED and not actor.is_approved:
            if config.debug:
                logger.debug(
                    "Ignoring command %s from unapproved user: %s (%s)",
                    invocation.trigger,
                    actor.username,
                    actor.user_id,
                )
            return False

        if spec.access == AccessLevel.ADMIN:
            if not actor.is_approved:
                if config.debug:
                    logger.debug(
                        "Ignoring admin command %s from unapproved user: "
                        "%s (%s)",
                        invocation.trigger,
                        actor.username,
                        actor.user_id,
                    )
                return False
            if not actor.is_admin:
                await api.create_post(
                    channel_id,
                    f"⚠️ Permission denied: Only administrators can use the "
                    f"`!{spec.name}` command.",
                    root_id=root_id,
                )
                return True

        session_key = get_session_key(effective.user_id, root_id)
        ctx = CommandContext(
            invocation=invocation,
            post=post,
            channel_id=channel_id,
            root_id=root_id,
            is_dm=is_dm,
            is_mentioned=is_mentioned,
            actor=actor,
            effective=effective,
            session_key=session_key,
            config=config,
            api=api,
            user_store=user_store,
            sessions=sessions,
            agent_pool=agent_pool,
            registry=self,
            bot_id=bot_id,
            bot_username=bot_username,
            shutdown_callback=shutdown_callback,
        )
        result = await spec.handler(ctx)
        if result and result.reply:
            await ctx.reply(result.reply)
        return True
