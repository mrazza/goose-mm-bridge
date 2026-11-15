from commands.models import AccessLevel
from commands.models import ChannelScope
from commands.models import CommandContext
from commands.models import CommandResult
from commands.models import CommandSpec
from logging_config import get_logger

logger = get_logger(__name__)


async def handle_stop(ctx: CommandContext) -> CommandResult:
    """Cancels any active prompt and background task for the current thread."""
    interrupted = False
    session = ctx.sessions.get(ctx.session_key)
    if session is not None and session.acp_session_id is not None:
        logger.info("Interruption requested for %s", ctx.session_key)
        linux_user = session.linux_user or ctx.effective.linux_user
        agent_name = session.agent_name or ctx.config.default_agent
        client = ctx.agent_pool.find_client(linux_user, agent_name)
        if client is not None:
            await client.cancel_prompt(session.acp_session_id)
            interrupted = True

    active_task = ctx.sessions.active_tasks.get(ctx.session_key)
    if active_task is not None:
        active_task.cancel()
        interrupted = True

    if interrupted:
        return CommandResult("🛑 *Prompt cancelled.*")
    return CommandResult()


async def handle_context(ctx: CommandContext) -> CommandResult:
    """Reports context window token usage for the current thread session."""
    session = ctx.sessions.get(ctx.session_key)
    if session is None:
        return CommandResult("ℹ️ *No active session for this thread.*")

    client = ctx.agent_pool.find_client(session.linux_user, session.agent_name)
    if client is None:
        return CommandResult("⚠️ *Agent client not found for this session.*")

    usage = (client.context_usage.get(session.acp_session_id)
             if session.acp_session_id else None)
    if not usage:
        return CommandResult(
            "ℹ️ *No context usage information available yet for this session.*")

    used = usage.get("used", 0)
    size = usage.get("size", 0)
    percent = (used / size * 100) if size > 0 else 0

    msg = ("📊 **Context Window Usage**\n"
           f"- Used: `{used:,}` tokens\n"
           f"- Total: `{size:,}` tokens\n"
           f"- Usage: `{percent:.1f}%`")
    return CommandResult(msg)


SESSION_COMMAND_SPECS = (
    CommandSpec(
        name="stop",
        handler=handle_stop,
        usage="!stop",
        description="Immediately cancel the active prompt in this thread",
        access=AccessLevel.PUBLIC,
        scope=ChannelScope.THREAD_OR_MENTION,
    ),
    CommandSpec(
        name="context",
        handler=handle_context,
        usage="!context",
        description="Show context window token usage for this thread",
        access=AccessLevel.PUBLIC,
        scope=ChannelScope.THREAD_OR_MENTION,
    ),
)
