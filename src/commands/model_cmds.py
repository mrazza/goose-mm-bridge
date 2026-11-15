from typing import Any, Dict, Optional, Set

from commands.models import AccessLevel
from commands.models import ChannelScope
from commands.models import CommandContext
from commands.models import CommandResult
from commands.models import CommandSpec


def _can_control_models(ctx: CommandContext) -> bool:
    user_config = ctx.effective.user_config
    if not getattr(user_config, "allow_model_control", True):
        return False
    allowed = getattr(user_config, "model_control_users", None)
    if allowed is not None:
        return (ctx.effective.user_id in allowed or
                ctx.effective.username in allowed)
    return True


def _extract_model_ids(available_models: list) -> Set[str]:
    known_ids: Set[str] = set()
    for model in available_models:
        if isinstance(model, dict):
            mid = model.get("id") or model.get("modelId") or model.get("name")
            if mid:
                known_ids.add(mid)
        elif isinstance(model, str):
            known_ids.add(model)
    return known_ids


def _format_model_status(
    *,
    current_model: Optional[str],
    available_models: list,
    status_desc: str,
) -> str:
    display_model = (f"`{current_model}`"
                     if current_model else "*(not configured)*")
    lines = [
        "🤖 **Model Status**",
        f"- Current model: {display_model} (*{status_desc}*)",
    ]

    if available_models:
        lines.append("")
        lines.append("**Available Models**")
        for m in available_models:
            if isinstance(m, dict):
                m_id = m.get("id") or m.get("modelId") or m.get("name")
                m_name = m.get("name")
                m_desc = m.get("description")
                active_flag = " (*active*)" if m_id == current_model else ""
                extra = ""
                if m_name and m_name != m_id:
                    extra += f": {m_name}"
                if m_desc and m_desc != m_name:
                    extra += f" - {m_desc}"
                lines.append(f"- `{m_id}`{extra}{active_flag}")
            else:
                active_flag = " (*active*)" if m == current_model else ""
                lines.append(f"- `{m}`{active_flag}")

    return "\n".join(lines) + "\n"


async def handle_model(ctx: CommandContext) -> CommandResult:
    """Queries or switches the active/staged model for the current thread."""
    can_control = _can_control_models(ctx)
    is_query = (ctx.invocation.trigger == "!models" or
                not ctx.invocation.raw_args)

    session = ctx.sessions.get(ctx.session_key)
    agent_name = (session.agent_name if session and session.agent_name else
                  ctx.user_store.get_default_agent(ctx.effective.user_id))
    if ctx.config.agents and agent_name not in ctx.config.agents:
        agent_name = ctx.config.default_agent

    client = ctx.agent_pool.get_or_create(ctx.effective.linux_user, agent_name)
    has_active_session = bool(session and session.acp_session_id is not None)
    sid = session.acp_session_id if has_active_session else None
    models_info: Optional[Dict[str, Any]] = (client.get_models(sid) if hasattr(
        client, "get_models") else None)

    if is_query:
        current_model = None
        if has_active_session:
            if models_info and models_info.get("current_model"):
                current_model = models_info["current_model"]
            elif session and session.model_name:
                current_model = session.model_name
        elif session and session.model_name:
            current_model = session.model_name

        if not current_model:
            current_model = ctx.effective.user_config.get_agent_default_model(
                agent_name)

        available_models = (models_info.get("available_models", [])
                            if models_info else [])

        if not can_control:
            status_desc = "locked by administrator"
        elif has_active_session:
            status_desc = "active in thread"
        elif session and session.model_name:
            status_desc = "staged for next session"
        else:
            status_desc = "default from environment"

        return CommandResult(
            _format_model_status(
                current_model=current_model,
                available_models=available_models,
                status_desc=status_desc,
            ))

    if not can_control:
        return CommandResult(
            "⚠️ *Permission denied: Model switching is disabled for your "
            "account. You are locked to your configured model.*")

    target_model = ctx.invocation.raw_args.strip()
    if models_info and models_info.get("available_models"):
        known_ids = _extract_model_ids(models_info["available_models"])
        if known_ids and target_model not in known_ids:
            return CommandResult(
                f"⚠️ *Unknown model `{target_model}`. Use `!model` to see "
                f"available models.*")

    if has_active_session and sid and session:
        try:
            await client.set_model(sid, target_model)
            session.model_name = target_model
            ctx.sessions.touch(ctx.session_key)
            return CommandResult(
                f"🔄 *Switched model for this thread to `{target_model}`.*")
        except Exception as e:  # pylint: disable=broad-exception-caught
            return CommandResult(
                f"⚠️ *Failed to switch model to `{target_model}`: {e}*")

    ctx.sessions.stage_model(ctx.effective.user_id, ctx.root_id, target_model)
    return CommandResult(
        f"🎯 *Model set to `{target_model}` for this thread. It will apply "
        f"when the session begins.*")


MODEL_COMMAND_SPECS = (CommandSpec(
    name="model",
    aliases=("models",),
    handler=handle_model,
    usage="!model [<name>]",
    description="List available models or switch model for this thread",
    access=AccessLevel.APPROVED,
    scope=ChannelScope.THREAD_OR_MENTION,
),)
