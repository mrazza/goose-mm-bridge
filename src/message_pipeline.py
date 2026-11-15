import asyncio
from typing import Any, Awaitable, Callable, Optional, Set, Tuple

from acp.pool import AgentClientPool
from commands.registry import CommandRegistry
from config import Config
from logging_config import get_logger
from sessions import SessionManager
from streaming import MattermostStreamRenderer
from user_store import UserStore
from utils import clean_message
from utils import get_session_key

logger = get_logger(__name__)


class PromptBuilder:
    """Pure helpers for constructing user prompts and system context."""

    @staticmethod
    def format_user_message(
        raw_message: str,
        bot_mention: Optional[str],
        username: str,
        file_ids: Optional[list] = None,
    ) -> str:
        """Formats a Mattermost post for ACP consumption."""
        cleaned = clean_message(raw_message, bot_mention)
        if file_ids:
            cleaned = f"[Has {len(file_ids)} attachment(s)] {cleaned}"
        return f"[Sender: @{username}] {cleaned}"

    @staticmethod
    def build_context_header(channel_id: str, root_id: str) -> str:
        """Builds the initial Mattermost channel/thread context preamble."""
        return (
            f"SYSTEM: Mattermost Channel ID: {channel_id}, "
            f"Root Post ID (Thread ID): {root_id}. You can use these IDs with "
            f"your tools to fetch more context about the current "
            f"channel/thread if needed.")

    @staticmethod
    def build_prompt(
        formatted_message: str,
        context_header: str,
        *,
        is_new_session: bool,
        thread_size: int,
        processed_count: int,
        had_catchup_hint: bool,
    ) -> Tuple[str, bool]:
        """Builds the prompt text with optional thread catch-up hints."""
        prompt_text = (f"{context_header}\n\n{formatted_message}"
                       if is_new_session else formatted_message)
        new_messages_count = max(0, thread_size - processed_count - 1)
        next_had_hint = had_catchup_hint

        if new_messages_count > 0:
            if is_new_session:
                hint = (
                    f"SYSTEM: You have joined an existing thread with "
                    f"{new_messages_count} earlier messages. Use your tools if "
                    f"you need to catch up on the history.")
            else:
                hint = (
                    f"SYSTEM: There are {new_messages_count} new messages in "
                    f"this thread since your last response. Use your tools if "
                    f"you need to catch up.")
            prompt_text = f"{hint}\n\n{prompt_text}"
            next_had_hint = True
        elif had_catchup_hint:
            prompt_text = (f"SYSTEM: You are now caught up with the thread."
                           f"\n\n{prompt_text}")
            next_had_hint = False

        return prompt_text, next_had_hint

    @staticmethod
    def build_retry_prompt(formatted_message: str, context_header: str) -> str:
        """Builds a recovery prompt after an unexpected ACP session reset."""
        retry_context = (
            f"{context_header} NOTE: The previous session for this thread "
            f"terminated unexpectedly. Context from earlier in this "
            f"conversation has been lost, but you can use the IDs above to try "
            f"to recover history if needed.")
        return f"{retry_context}\n\n{formatted_message}"


class MessagePipeline:
    """Triages inbound Mattermost posts into commands or agent turns."""

    def __init__(
        self,
        *,
        config: Config,
        api: Any,
        user_store: UserStore,
        agent_pool: AgentClientPool,
        sessions: SessionManager,
        renderer: MattermostStreamRenderer,
        commands: CommandRegistry,
        background_tasks: Optional[Set[asyncio.Task]] = None,
        shutdown_callback: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self.config = config
        self.api = api
        self.user_store = user_store
        self.agent_pool = agent_pool
        self.sessions = sessions
        self.renderer = renderer
        self.commands = commands
        self.background_tasks = (background_tasks
                                 if background_tasks is not None else set())
        self.shutdown_callback = shutdown_callback
        self.bot_id: Optional[str] = None
        self.bot_username: Optional[str] = None
        self.bot_mention: Optional[str] = None

    def set_bot_identity(
        self,
        bot_id: Optional[str],
        bot_username: Optional[str],
        bot_mention: Optional[str],
    ) -> None:
        """Updates the resolved bot user metadata."""
        self.bot_id = bot_id
        self.bot_username = bot_username
        self.bot_mention = bot_mention

    async def process_post(self, post: dict, channel_map: dict) -> None:
        """Processes a single inbound Mattermost post."""
        sender_id = post["user_id"]
        if sender_id == self.bot_id:
            return

        message = post.get("message", "").strip()
        if not message:
            return

        cid = post["channel_id"]
        channel = channel_map.get(cid)
        is_dm = bool(channel and channel.get("type") == "D")
        is_mentioned = bool(self.bot_mention and self.bot_mention in message)
        root_id = post.get("root_id") or post["id"]

        invocation = self.commands.parse(message, self.bot_mention)
        if invocation is not None:
            await self.commands.dispatch(
                invocation,
                post=post,
                is_dm=is_dm,
                is_mentioned=is_mentioned,
                root_id=root_id,
                config=self.config,
                api=self.api,
                user_store=self.user_store,
                sessions=self.sessions,
                agent_pool=self.agent_pool,
                bot_id=self.bot_id,
                bot_username=self.bot_username,
                shutdown_callback=self.shutdown_callback,
            )
            return

        # Only non-command messages advance thread catch-up tracking.
        self.sessions.record_thread_post(root_id)

        if not is_dm and not is_mentioned:
            return

        actor, effective = await self.user_store.resolve_identities(
            sender_id, resolve_impersonation=True)

        if not actor.is_approved:
            if self.config.debug:
                logger.debug(
                    "Ignoring message from unapproved user: %s (%s)",
                    actor.username,
                    actor.user_id,
                )
            return

        if self.config.require_user_mapping and not effective.linux_user:
            logger.warning(
                "Rejecting approved user %s: No Linux user mapping and "
                "REQUIRE_USER_MAPPING=true",
                effective.username,
            )
            await self.api.create_post(
                cid,
                "⚠️ Your account is approved but has no assigned OS-level "
                "isolation profile. Please contact an administrator.",
                root_id=root_id,
            )
            return

        await self.sessions.ensure_thread_counter(root_id, self.api,
                                                  self.bot_id)

        task = asyncio.create_task(
            self.handle_message(
                post,
                effective.linux_user,
                effective.username,
                sender_id=effective.user_id,
            ))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def handle_message(
        self,
        post: dict,
        linux_user: Optional[str],
        username: str,
        sender_id: Optional[str] = None,
    ) -> None:
        """Executes a prompt turn against the thread's ACP session."""
        sender_id = sender_id or post["user_id"]
        raw_message = post.get("message", "").strip()
        if not raw_message:
            return

        channel_id = post["channel_id"]
        root_id = post.get("root_id") or post["id"]
        session_key = get_session_key(sender_id, root_id)
        context_header = PromptBuilder.build_context_header(channel_id, root_id)

        self.sessions.register_active_task(session_key, asyncio.current_task())
        lock = self.sessions.get_lock(session_key)

        existing = self.sessions.get(session_key)
        agent_name = (existing.agent_name if existing and existing.agent_name
                      else self.user_store.get_default_agent(sender_id))
        if self.config.agents and agent_name not in self.config.agents:
            agent_name = self.config.default_agent

        client = self.agent_pool.get_or_create(linux_user, agent_name)

        async with lock:
            try:
                formatted_message = PromptBuilder.format_user_message(
                    raw_message,
                    self.bot_mention,
                    username,
                    file_ids=post.get("file_ids"),
                )

                real_sender_id = post["user_id"]
                if real_sender_id != sender_id:
                    logger.info(
                        "Admin (%s) impersonating %s (%s) says: %s...",
                        real_sender_id,
                        username,
                        sender_id,
                        formatted_message[:100],
                    )
                else:
                    logger.info(
                        "User %s (%s) says: %s...",
                        username,
                        sender_id,
                        formatted_message[:100],
                    )

                session_data = self.sessions.get(session_key)
                is_new_session = False
                if session_data is None or session_data.acp_session_id is None:
                    logger.info(
                        "Creating new Agent '%s' session for %s",
                        agent_name,
                        session_key,
                    )
                    target_model = (session_data.model_name
                                    if session_data else None)
                    actual_model = target_model
                    sid = await client.create_session()
                    if target_model:
                        try:
                            await client.set_model(sid, target_model)
                        except Exception as e:  # pylint: disable=broad-exception-caught
                            logger.error(
                                "Error applying staged model %s: %s",
                                target_model,
                                e,
                            )
                            await self.api.create_post(
                                channel_id,
                                f"⚠️ *Failed to apply staged model "
                                f"`{target_model}`: {e}. Falling back to "
                                f"default model.*",
                                root_id=root_id,
                            )
                            actual_model = None
                    session_data = self.sessions.activate(
                        user_id=sender_id,
                        root_id=root_id,
                        acp_session_id=sid,
                        linux_user=linux_user,
                        agent_name=agent_name,
                        model_name=actual_model,
                        had_catchup_hint=True,
                    )
                    is_new_session = True
                else:
                    self.sessions.touch(session_key)

                thread_size = self.sessions.thread_counters.get(root_id, 1)
                prompt_text, next_had_hint = PromptBuilder.build_prompt(
                    formatted_message,
                    context_header,
                    is_new_session=is_new_session,
                    thread_size=thread_size,
                    processed_count=session_data.processed_count,
                    had_catchup_hint=session_data.had_catchup_hint,
                )
                session_data.had_catchup_hint = next_had_hint
                if (self.config.debug and max(
                        0, thread_size - session_data.processed_count - 1) > 0):
                    logger.debug(
                        "Merging catch-up hint for %s",
                        session_key,
                    )

                try:
                    await self.renderer.stream_response(
                        client,
                        session_data.acp_session_id,
                        prompt_text,
                        channel_id,
                        root_id,
                    )
                    session_data.processed_count = thread_size
                except (ValueError, RuntimeError, asyncio.TimeoutError) as err:
                    logger.warning(
                        "Session %s lost, retrying once: %s",
                        session_key,
                        err,
                    )
                    agent_display = ("Goose" if agent_name == "goose" else
                                     f"Agent '{agent_name}'")
                    await self.api.create_post(
                        channel_id,
                        f"🔄 *Notice: Connection to {agent_display} was reset. "
                        f"I am starting a fresh session for this thread.*",
                        root_id=root_id,
                    )
                    prev_model = session_data.model_name
                    retry_sid = await client.create_session()
                    if prev_model:
                        try:
                            await client.set_model(retry_sid, prev_model)
                        except Exception as model_err:  # pylint: disable=broad-exception-caught
                            logger.error(
                                "Error re-applying model %s on retry: %s",
                                prev_model,
                                model_err,
                            )
                    session_data = self.sessions.activate(
                        user_id=sender_id,
                        root_id=root_id,
                        acp_session_id=retry_sid,
                        linux_user=linux_user,
                        agent_name=agent_name,
                        model_name=prev_model,
                        had_catchup_hint=False,
                    )
                    retry_prompt = PromptBuilder.build_retry_prompt(
                        formatted_message, context_header)
                    await self.renderer.stream_response(
                        client,
                        session_data.acp_session_id,
                        retry_prompt,
                        channel_id,
                        root_id,
                    )
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error(
                    "Error handling message for %s: %s",
                    session_key,
                    e,
                )
                await self.api.create_post(
                    channel_id,
                    f"⚠️ Sorry, I encountered an error: {str(e)}",
                    root_id=root_id,
                )
            finally:
                self.sessions.clear_active_task(session_key,
                                                asyncio.current_task())
