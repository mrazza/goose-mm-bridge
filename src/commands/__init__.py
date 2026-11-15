from commands.admin_cmds import ADMIN_COMMAND_SPECS
from commands.agent_cmds import AGENT_COMMAND_SPECS
from commands.model_cmds import MODEL_COMMAND_SPECS
from commands.models import AccessLevel
from commands.models import ChannelScope
from commands.models import CommandContext
from commands.models import CommandInvocation
from commands.models import CommandResult
from commands.models import CommandSpec
from commands.registry import CommandRegistry
from commands.registry import handle_help
from commands.session_cmds import SESSION_COMMAND_SPECS


def create_default_registry() -> CommandRegistry:
    """Builds the default CommandRegistry containing all bridge commands."""
    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="help",
            handler=handle_help,
            usage="!help",
            description="Show available commands",
            access=AccessLevel.APPROVED,
            scope=ChannelScope.THREAD_OR_MENTION,
            resolves_impersonation=False,
        ))
    for spec in (
            *SESSION_COMMAND_SPECS,
            *AGENT_COMMAND_SPECS,
            *MODEL_COMMAND_SPECS,
            *ADMIN_COMMAND_SPECS,
    ):
        registry.register(spec)
    return registry


__all__ = [
    "AccessLevel",
    "ChannelScope",
    "CommandContext",
    "CommandInvocation",
    "CommandRegistry",
    "CommandResult",
    "CommandSpec",
    "create_default_registry",
]
