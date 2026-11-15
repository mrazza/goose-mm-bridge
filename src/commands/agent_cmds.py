from commands.models import AccessLevel
from commands.models import ChannelScope
from commands.models import CommandContext
from commands.models import CommandResult
from commands.models import CommandSpec


async def handle_agents(ctx: CommandContext) -> CommandResult:
    """Lists available configured agents with thread/default indicators."""
    session = ctx.sessions.get(ctx.session_key)
    active_agent = session.agent_name if session else None
    default_agent = ctx.user_store.get_default_agent(ctx.effective.user_id)

    lines = ["🤖 **Available Agents**"]
    for name in sorted((ctx.config.agents or {}).keys()):
        status = []
        if name == active_agent:
            status.append("active in thread")
        if name == default_agent:
            status.append("your default")

        status_str = f" (*{', '.join(status)}*)" if status else ""
        lines.append(f"- `{name}`{status_str}")

    return CommandResult("\n".join(lines) + "\n")


async def handle_agent_default(ctx: CommandContext) -> CommandResult:
    """Shows or updates the persistent default agent for the user."""
    if not ctx.invocation.raw_args:
        default_agent = ctx.user_store.get_default_agent(ctx.effective.user_id)
        return CommandResult(
            f"👤 *Your persistent default agent is `{default_agent}`.*")

    target_agent = ctx.invocation.raw_args.strip().lower()
    if target_agent not in (ctx.config.agents or {}):
        return CommandResult(
            f"⚠️ *Unknown agent `{target_agent}`. Use `!agents` to see "
            f"available agents.*")

    ctx.user_store.set_default_agent(ctx.effective.user_id, target_agent)
    return CommandResult(
        f"👤 *Persistent default agent set to `{target_agent}` for your "
        f"account.*")


async def handle_agent(ctx: CommandContext) -> CommandResult:
    """Shows or switches the agent assigned to the current thread."""
    if not ctx.invocation.raw_args:
        session = ctx.sessions.get(ctx.session_key)
        if session and session.agent_name:
            return CommandResult(
                f"🤖 *Active agent for this thread is `{session.agent_name}`.*")
        default_agent = ctx.user_store.get_default_agent(ctx.effective.user_id)
        return CommandResult(
            f"🤖 *No active session for this thread. The next prompt will use "
            f"your default agent `{default_agent}`.*")

    target_agent = ctx.invocation.raw_args.strip().lower()
    if target_agent not in (ctx.config.agents or {}):
        return CommandResult(
            f"⚠️ *Unknown agent `{target_agent}`. Use `!agents` to see "
            f"available agents.*")

    await ctx.sessions.stage_agent(ctx.effective.user_id, ctx.root_id,
                                   target_agent)
    return CommandResult(
        f"🔄 *Switched agent for this thread to `{target_agent}`. The next "
        f"message will start a fresh session.*")


AGENT_COMMAND_SPECS = (
    CommandSpec(
        name="agents",
        handler=handle_agents,
        usage="!agents",
        description="List available agents and active/default status",
        access=AccessLevel.APPROVED,
        scope=ChannelScope.THREAD_OR_MENTION,
    ),
    CommandSpec(
        name="agent",
        handler=handle_agent,
        usage="!agent [<name>]",
        description="Show or switch the active agent for this thread",
        access=AccessLevel.APPROVED,
        scope=ChannelScope.THREAD_OR_MENTION,
    ),
    CommandSpec(
        name="agent-default",
        handler=handle_agent_default,
        usage="!agent-default [<name>]",
        description="Show or set your persistent default agent",
        access=AccessLevel.APPROVED,
        scope=ChannelScope.THREAD_OR_MENTION,
    ),
)
