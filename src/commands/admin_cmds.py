import sys

from commands.models import AccessLevel
from commands.models import ChannelScope
from commands.models import CommandContext
from commands.models import CommandResult
from commands.models import CommandSpec


async def handle_impersonate(ctx: CommandContext) -> CommandResult:
    """Allows administrators to impersonate another user or clear state."""
    raw_args = ctx.invocation.raw_args.strip()
    if not raw_args or raw_args.lower() in ("clear", "off", "stop"):
        old_target = ctx.user_store.clear_impersonation(ctx.actor.user_id)
        if old_target:
            return CommandResult(
                f"👤 *Impersonation cleared. You are no longer impersonating "
                f"@{old_target['username']}.*")
        return CommandResult(
            "ℹ️ *You are not currently impersonating any user.*")

    target = raw_args.lstrip("@")
    target_user = await ctx.api.get_user(target)
    if not target_user:
        users = await ctx.api.search_users(target)
        if users:
            for u in users:
                if u.get("username") == target or u.get("id") == target:
                    target_user = u
                    break
            if not target_user:
                target_user = users[0]

    if not target_user:
        return CommandResult(f"⚠️ *Error: User `{target}` could not be found.*")

    target_id = target_user["id"]
    target_username = target_user["username"]

    if target_id == ctx.bot_id or target_username == ctx.bot_username:
        return CommandResult(
            "⚠️ *Error: You cannot impersonate the bot itself.*")

    ctx.user_store.set_impersonation(ctx.actor.user_id, target_id,
                                     target_username)
    return CommandResult(
        f"👤 *You are now impersonating @{target_username} (`{target_id}`). "
        f"All subsequent prompts will run in their context.*")


async def handle_restart(ctx: CommandContext) -> CommandResult:
    """Gracefully shuts down and exits so the supervisor restarts the bridge."""
    await ctx.reply(
        "🔄 *Restarting goose-mm-bridge service... Gracefully closing active "
        "sessions.*")
    if ctx.shutdown_callback is not None:
        await ctx.shutdown_callback()
    sys.exit(0)


ADMIN_COMMAND_SPECS = (
    CommandSpec(
        name="impersonate",
        handler=handle_impersonate,
        usage="!impersonate [<@user|id|clear>]",
        description="Operate as another user or clear impersonation",
        access=AccessLevel.ADMIN,
        scope=ChannelScope.THREAD_OR_MENTION,
        resolves_impersonation=False,
    ),
    CommandSpec(
        name="restart",
        handler=handle_restart,
        usage="!restart",
        description="Gracefully close active sessions and restart the bridge",
        access=AccessLevel.ADMIN,
        scope=ChannelScope.DM_OR_MENTION,
        resolves_impersonation=False,
    ),
)
