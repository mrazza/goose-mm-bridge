import asyncio
from datetime import datetime
import os
import time
from typing import Dict, List, Optional, Tuple

from config import default_config
from acp_client import ACPClient
from mattermost_api import MattermostAPI
from utils import clean_message
from utils import get_session_key
from utils import load_user_mapping

CACHE_TTL = 60  # Update cache every 60 seconds
THINKING_MSG = ":thinking_face: **Thinking...**"


class GooseClientsCompatDict(dict):

    def __init__(self, bridge):
        self._bridge = bridge
        super().__init__()

    def __getitem__(self, key):
        return self._bridge.agent_clients.get(
            (key, self._bridge.config.default_agent))

    def __setitem__(self, key, value):
        self._bridge.agent_clients[(key,
                                    self._bridge.config.default_agent)] = value

    def __contains__(self, key):
        return (key,
                self._bridge.config.default_agent) in self._bridge.agent_clients

    def get(self, key, default=None):
        return self._bridge.agent_clients.get(
            (key, self._bridge.config.default_agent), default)

    def values(self):
        return self._bridge.agent_clients.values()


class MattermostBridge:
    """Manages the connection between Mattermost and ACP agents."""

    def __init__(self,
                 api=None,
                 config=None,
                 goose_client_factory=None,
                 agent_client_factory=None):
        self.config = config or default_config
        self.api = api or MattermostAPI(config=self.config)

        if goose_client_factory is not None:
            self.agent_client_factory = lambda user, agent: goose_client_factory(
                user)
        else:
            self.agent_client_factory = agent_client_factory or (
                lambda user, agent: ACPClient(user, agent, config=self.config))

        self.agent_clients: Dict[Tuple[str, str], ACPClient] = {}
        self.goose_clients = GooseClientsCompatDict(self)
        self.sessions = {}
        self.active_tasks = {}
        self.session_locks = {}
        self.channels_cache: List[dict] = []
        self.last_cache_update = 0
        self.last_since = int(time.time() * 1000)
        self.bot_id = None
        self.bot_username = None
        self.bot_mention = None
        self.background_tasks = set()
        self.thread_counters = {}  # Track message counts per thread
        self.impersonations = {
        }  # Admin impersonation state: admin_id -> {"id": target_id, "username": target_username}

    async def initialize(self) -> bool:
        """Initializes the bridge by connecting to Mattermost."""
        me = await self.api.get_me()
        if not me:
            print(
                f"[{datetime.now()}] Failed to connect to Mattermost. Check your URL and TOKEN."
            )
            return False

        self.bot_id = me["id"]
        self.bot_username = me["username"]
        self.bot_mention = f"@{self.bot_username}"
        print(
            f"[{datetime.now()}] Connected as {self.bot_username} ({self.bot_id})"
        )

        user_mapping = load_user_mapping(self.config.user_mapping_file)
        if not user_mapping:
            print(
                f"[{datetime.now()}] WARNING: No user mapping found. Bridge will run as current user for all requests."
            )

        return True

    async def _update_channel_cache(self):
        """Updates the internal cache of Mattermost channels."""
        current_time = time.time()
        if not self.channels_cache or current_time - self.last_cache_update > CACHE_TTL:
            if self.config.debug:
                print(
                    f"[{datetime.now()}] Updating Mattermost channels cache...")

            channels = await self.api.get_direct_channels() or []
            teams = await self.api.get_my_teams() or []
            for team in teams:
                team_channels = await self.api.get_my_channels(team["id"]) or []
                channels.extend(team_channels)

            self.channels_cache = list({c["id"]: c for c in channels}.values())
            self.last_cache_update = current_time

    async def _handle_stop_command(self, post: dict):
        """Handles the !stop command to cancel active prompts."""
        sender_id = post["user_id"]
        cid = post["channel_id"]
        root_id = post.get("root_id") or post["id"]

        eff_id = sender_id
        if sender_id in self.impersonations:
            eff_id = self.impersonations[sender_id]["id"]

        session_key = get_session_key(eff_id, root_id)

        interrupted = False
        if session_key in self.sessions:
            print(
                f"[{datetime.now()}] Interruption requested for {session_key}")
            sid = self.sessions[session_key]["id"]
            user_mapping = load_user_mapping(self.config.user_mapping_file)
            user_info = await self.api.get_user(eff_id)
            username = user_info.get("username") if user_info else "unknown"
            linux_user = user_mapping.get(eff_id) or user_mapping.get(username)
            agent_name = self.sessions[session_key].get(
                "agent_name") or self.config.default_agent
            client_key = (linux_user, agent_name)

            if client_key in self.agent_clients and sid is not None:
                await self.agent_clients[client_key].cancel_prompt(sid)
                interrupted = True

        if session_key in self.active_tasks:
            self.active_tasks[session_key].cancel()
            interrupted = True

        if interrupted:
            await self.api.create_post(cid,
                                       "🛑 *Prompt cancelled.*",
                                       root_id=root_id)

    async def _handle_context_command(self, post: dict):
        """Handles the !context command to show context window usage."""
        sender_id = post["user_id"]
        cid = post["channel_id"]
        root_id = post.get("root_id") or post["id"]

        eff_id = sender_id
        if sender_id in self.impersonations:
            eff_id = self.impersonations[sender_id]["id"]

        session_key = get_session_key(eff_id, root_id)

        if session_key not in self.sessions:
            await self.api.create_post(
                cid, "ℹ️ *No active session for this thread.*", root_id=root_id)
            return

        session_data = self.sessions[session_key]
        sid = session_data["id"]
        target_linux_user = session_data["linux_user"]
        agent_name = session_data.get("agent_name") or self.config.default_agent
        client_key = (target_linux_user, agent_name)

        if client_key not in self.agent_clients:
            await self.api.create_post(
                cid,
                "⚠️ *Agent client not found for this session.*",
                root_id=root_id)
            return

        client = self.agent_clients[client_key]
        usage = client.context_usage.get(sid)

        if not usage:
            await self.api.create_post(
                cid,
                "ℹ️ *No context usage information available yet for this session.*",
                root_id=root_id)
            return

        used = usage.get("used", 0)
        size = usage.get("size", 0)
        percent = (used / size * 100) if size > 0 else 0

        msg = f"📊 **Context Window Usage**\n"
        msg += f"- Used: `{used:,}` tokens\n"
        msg += f"- Total: `{size:,}` tokens\n"
        msg += f"- Usage: `{percent:.1f}%`"

        await self.api.create_post(cid, msg, root_id=root_id)

    async def _handle_impersonate_command(self, post: dict, cleaned_msg: str):
        """Handles the !impersonate command for administrators."""
        sender_id = post["user_id"]
        cid = post["channel_id"]
        root_id = post.get("root_id") or post["id"]

        user_info = await self.api.get_user(sender_id)
        username = user_info.get("username") if user_info else "unknown"

        is_admin = False
        if self.config.admin_users:
            if sender_id in self.config.admin_users or username in self.config.admin_users:
                is_admin = True

        if not is_admin:
            await self.api.create_post(
                cid,
                "⚠️ Permission denied: Only administrators can use the `!impersonate` command.",
                root_id=root_id)
            return

        parts = cleaned_msg.split(maxsplit=1)
        if len(parts) == 1 or parts[1].strip().lower() in [
                "clear", "off", "stop"
        ]:
            if sender_id in self.impersonations:
                old_target = self.impersonations.pop(sender_id)
                await self.api.create_post(
                    cid,
                    f"👤 *Impersonation cleared. You are no longer impersonating @{old_target['username']}.*",
                    root_id=root_id)
            else:
                await self.api.create_post(
                    cid,
                    "ℹ️ *You are not currently impersonating any user.*",
                    root_id=root_id)
            return

        target = parts[1].strip().lstrip("@")
        target_user = None

        # 1. Try resolving by user ID
        target_user = await self.api.get_user(target)
        if not target_user:
            # 2. Try searching by username
            users = await self.api.search_users(target)
            if users:
                for u in users:
                    if u.get("username") == target or u.get("id") == target:
                        target_user = u
                        break
                if not target_user:
                    target_user = users[0]

        if not target_user:
            await self.api.create_post(
                cid,
                f"⚠️ *Error: User `{target}` could not be found.*",
                root_id=root_id)
            return

        target_id = target_user["id"]
        target_username = target_user["username"]

        if target_id == self.bot_id or target_username == self.bot_username:
            await self.api.create_post(
                cid,
                "⚠️ *Error: You cannot impersonate the bot itself.*",
                root_id=root_id)
            return

        self.impersonations[sender_id] = {
            "id": target_id,
            "username": target_username
        }

        await self.api.create_post(
            cid,
            f"👤 *You are now impersonating @{target_username} (`{target_id}`). All subsequent prompts will run in their context.*",
            root_id=root_id)

    def _get_user_default_agent(self, user_id: str) -> str:
        """Retrieves the persistent default agent for a user."""
        preferences_path = self.config.user_agent_preferences_file
        if not os.path.isabs(preferences_path):
            from config import project_root
            preferences_path = os.path.join(project_root, preferences_path)

        if os.path.exists(preferences_path):
            try:
                import json
                with open(preferences_path, 'r') as f:
                    prefs = json.load(f)
                    return prefs.get(user_id, self.config.default_agent)
            except Exception as e:
                print(f"Error loading user agent preferences: {e}")
        return self.config.default_agent

    def _set_user_default_agent(self, user_id: str, agent_name: str):
        """Saves the persistent default agent for a user."""
        preferences_path = self.config.user_agent_preferences_file
        if not os.path.isabs(preferences_path):
            from config import project_root
            preferences_path = os.path.join(project_root, preferences_path)

        prefs = {}
        if os.path.exists(preferences_path):
            try:
                import json
                with open(preferences_path, 'r') as f:
                    prefs = json.load(f)
            except Exception as e:
                print(f"Error loading user agent preferences for write: {e}")

        prefs[user_id] = agent_name
        try:
            import json
            with open(preferences_path, 'w') as f:
                json.dump(prefs, f, indent=2)
        except Exception as e:
            print(f"Error saving user agent preferences: {e}")

    async def _handle_agents_command(self, post: dict):
        """Handles the !agents command to list available agents."""
        sender_id = post["user_id"]
        cid = post["channel_id"]
        root_id = post.get("root_id") or post["id"]

        eff_id = sender_id
        if sender_id in self.impersonations:
            eff_id = self.impersonations[sender_id]["id"]

        session_key = get_session_key(eff_id, root_id)

        if session_key in self.sessions:
            active_agent = self.sessions[session_key].get("agent_name")
        else:
            active_agent = None

        default_agent = self._get_user_default_agent(eff_id)

        msg = "🤖 **Available Agents**\n"
        for name in sorted(self.config.agents.keys()):
            status = []
            if name == active_agent:
                status.append("active in thread")
            if name == default_agent:
                status.append("your default")

            status_str = f" (*{', '.join(status)}*)" if status else ""
            msg += f"- `{name}`{status_str}\n"

        await self.api.create_post(cid, msg, root_id=root_id)

    async def _handle_agent_default_command(self, post: dict, cleaned_msg: str):
        """Handles the !agent-default <name> command to set default agent."""
        sender_id = post["user_id"]
        cid = post["channel_id"]
        root_id = post.get("root_id") or post["id"]

        parts = cleaned_msg.split(maxsplit=1)
        if len(parts) < 2:
            eff_id = sender_id
            if sender_id in self.impersonations:
                eff_id = self.impersonations[sender_id]["id"]
            default_agent = self._get_user_default_agent(eff_id)
            await self.api.create_post(
                cid,
                f"👤 *Your persistent default agent is `{default_agent}`.*",
                root_id=root_id)
            return

        target_agent = parts[1].strip().lower()
        if target_agent not in self.config.agents:
            await self.api.create_post(
                cid,
                f"⚠️ *Unknown agent `{target_agent}`. Use `!agents` to see available agents.*",
                root_id=root_id)
            return

        eff_id = sender_id
        if sender_id in self.impersonations:
            eff_id = self.impersonations[sender_id]["id"]

        self._set_user_default_agent(eff_id, target_agent)
        await self.api.create_post(
            cid,
            f"👤 *Persistent default agent set to `{target_agent}` for your account.*",
            root_id=root_id)

    async def _handle_agent_command(self, post: dict, cleaned_msg: str):
        """Handles the !agent <name> command to switch agents in the thread."""
        sender_id = post["user_id"]
        cid = post["channel_id"]
        root_id = post.get("root_id") or post["id"]

        parts = cleaned_msg.split(maxsplit=1)
        if len(parts) < 2:
            eff_id = sender_id
            if sender_id in self.impersonations:
                eff_id = self.impersonations[sender_id]["id"]
            session_key = get_session_key(eff_id, root_id)
            if session_key in self.sessions:
                active_agent = self.sessions[session_key].get("agent_name")
                await self.api.create_post(
                    cid,
                    f"🤖 *Active agent for this thread is `{active_agent}`.*",
                    root_id=root_id)
            else:
                default_agent = self._get_user_default_agent(eff_id)
                await self.api.create_post(
                    cid,
                    f"🤖 *No active session for this thread. The next prompt will use your default agent `{default_agent}`.*",
                    root_id=root_id)
            return

        target_agent = parts[1].strip().lower()
        if target_agent not in self.config.agents:
            await self.api.create_post(
                cid,
                f"⚠️ *Unknown agent `{target_agent}`. Use `!agents` to see available agents.*",
                root_id=root_id)
            return

        eff_id = sender_id
        if sender_id in self.impersonations:
            eff_id = self.impersonations[sender_id]["id"]

        session_key = get_session_key(eff_id, root_id)

        # Cancel any active tasks running in this thread
        if session_key in self.active_tasks:
            self.active_tasks[session_key].cancel()

        # If there's an existing session, we must close it first
        if session_key in self.sessions:
            session_data = self.sessions.pop(session_key)
            sid = session_data["id"]
            old_agent = session_data.get("agent_name")
            target_linux_user = session_data["linux_user"]

            # Send session close request asynchronously to clean up
            if (target_linux_user,
                    old_agent) in self.agent_clients and sid is not None:
                client = self.agent_clients[(target_linux_user, old_agent)]
                if sid in client.session_queues:
                    del client.session_queues[sid]
                asyncio.create_task(
                    client.send_request("session/close", {"sessionId": sid}))

        if session_key in self.session_locks:
            del self.session_locks[session_key]

        # Pre-seed the new session entry so the bridge knows which agent to spawn next
        self.sessions[session_key] = {
            "id": None,  # will be created on the next message
            "linux_user": None,  # resolved on next message
            "agent_name": target_agent,
            "processed_count": 0,
            "had_catchup_hint": False
        }

        await self.api.create_post(
            cid,
            f"🔄 *Switched agent for this thread to `{target_agent}`. The next message will start a fresh session.*",
            root_id=root_id)

    async def _prune_sessions(self):
        """Prunes old sessions if the count exceeds MAX_SESSIONS."""
        if len(self.sessions) <= self.config.max_sessions:
            return

        prune_count = max(1, self.config.max_sessions // 5)
        keys_to_remove = list(self.sessions.keys())[:prune_count]
        for k in keys_to_remove:
            session_data = self.sessions.pop(k)
            sid = session_data["id"]
            target_linux_user = session_data["linux_user"]
            agent_name = session_data.get(
                "agent_name") or self.config.default_agent
            client_key = (target_linux_user, agent_name)

            if self.config.debug:
                print(f"DEBUG: Pruning old session for {k} ({sid})")

            if client_key in self.agent_clients and sid is not None:
                client = self.agent_clients[client_key]
                if sid in client.session_queues:
                    del client.session_queues[sid]
                asyncio.create_task(
                    client.send_request("session/close", {"sessionId": sid}))

            if k in self.session_locks:
                del self.session_locks[k]

    async def _stream_response_to_mattermost(self, goose: ACPClient, sid: str,
                                             msg: str, channel_id: str,
                                             root_id: str):
        """Streams a response from the agent to Mattermost."""
        thinking_post = None
        full_response = ""
        thinking_trace = ""
        last_thinking_text = ""
        title = ""
        last_update_time = 0

        # Create an initial thinking post to show immediate feedback
        thinking_post = await self.api.create_post(channel_id,
                                                   THINKING_MSG,
                                                   root_id=root_id)
        last_update_time = time.time()

        async for update in goose.prompt(sid, msg):
            if update["type"] == "thinking":
                thinking_trace += f"\n\n**Working**: {update['text']}"
                last_thinking_text = update["text"].strip()
            elif update["type"] == "tool":
                thinking_trace += f"\n\n**Using tool**: `{update['name']}`\n"
            elif update["type"] == "title":
                title = update['title']

            if len(thinking_trace) > 10000:
                thinking_trace = "... (truncated) ...\n" + thinking_trace[-8000:]

            if update["type"] == "content":
                full_response = update["text"]
            elif update["type"] == "final":
                full_response = update["text"]

            current_time = time.time()
            should_update = False
            if update["type"] == "final":
                should_update = True
            elif (current_time - last_update_time > 1.0) and (
                (self.config.goose_thinking_trace and thinking_trace) or
                    full_response):
                should_update = True

            if should_update:
                resp_msg = ""
                props = {}

                # Determine the message content
                if update["type"] != "final":
                    # While thinking/streaming
                    if self.config.goose_thinking_trace_simplified and last_thinking_text:
                        if title:
                            resp_msg = full_response or f":thinking_face: **Working on {title.lower()}...** *[{last_thinking_text}]*"
                        else:
                            # Simplified mode: Thinking... [Last action]
                            resp_msg = full_response or f"{THINKING_MSG} *[{last_thinking_text}]*"
                    else:
                        resp_msg = full_response or THINKING_MSG
                else:
                    # Final response
                    resp_msg = full_response

                # Determine if we add attachments
                if self.config.goose_thinking_trace and thinking_trace and not self.config.goose_thinking_trace_simplified:
                    props = {
                        "attachments": [{
                            "text": thinking_trace,
                            "title": "Thinking Trace",
                            "color": "#9b9b9b"
                        }]
                    }

                if not thinking_post:
                    thinking_post = await self.api.create_post(channel_id,
                                                               resp_msg,
                                                               root_id=root_id,
                                                               props=props)
                else:
                    await self.api.update_post(thinking_post["id"],
                                               resp_msg,
                                               props=props)
                last_update_time = current_time

    async def _handle_message(self,
                              post: dict,
                              linux_user: Optional[str],
                              username: str,
                              sender_id: Optional[str] = None):
        """Handles an incoming message from Mattermost."""
        sender_id = sender_id or post["user_id"]
        message = post.get("message", "").strip()
        if not message:
            return

        channel_id = post["channel_id"]
        root_id = post.get("root_id") or post["id"]
        session_key = get_session_key(sender_id, root_id)
        context_msg = f"SYSTEM: Mattermost Channel ID: {channel_id}, Root Post ID (Thread ID): {root_id}. You can use these IDs with your tools to fetch more context about the current channel/thread if needed."

        self.active_tasks[session_key] = asyncio.current_task()

        if session_key not in self.session_locks:
            self.session_locks[session_key] = asyncio.Lock()

        # Determine agent name
        if session_key in self.sessions:
            agent_name = self.sessions[session_key].get(
                "agent_name") or self._get_user_default_agent(sender_id)
        else:
            agent_name = self._get_user_default_agent(sender_id)

        if agent_name not in self.config.agents:
            agent_name = self.config.default_agent

        client_key = (linux_user, agent_name)
        if client_key not in self.agent_clients:
            self.agent_clients[client_key] = self.agent_client_factory(
                linux_user, agent_name)
        client = self.agent_clients[client_key]

        async with self.session_locks[session_key]:
            try:
                message = clean_message(message, self.bot_mention)

                # Align with get_thread_context formatting
                file_ids = post.get('file_ids', [])
                if file_ids:
                    message = f"[Has {len(file_ids)} attachment(s)] {message}"
                message = f"[Sender: @{username}] {message}"

                real_sender_id = post["user_id"]
                if real_sender_id != sender_id:
                    print(
                        f"[{datetime.now()}] Admin ({real_sender_id}) impersonating {username} ({sender_id}) says: {message[:100]}..."
                    )
                else:
                    print(
                        f"[{datetime.now()}] User {username} ({sender_id}) says: {message[:100]}..."
                    )

                is_new_session = False
                if session_key not in self.sessions or self.sessions[
                        session_key].get("id") is None:
                    print(
                        f"[{datetime.now()}] Creating new Agent '{agent_name}' session for {session_key}"
                    )

                    sid = await client.create_session()
                    self.sessions[session_key] = {
                        "id": sid,
                        "linux_user": linux_user,
                        "agent_name": agent_name,
                        "processed_count": 0,
                        # We mark the catchup hint to true so we clarify in a new thread whether
                        # any messages were missed.
                        "had_catchup_hint": True
                    }
                    is_new_session = True

                session_data = self.sessions[session_key]
                goose_sid = session_data["id"]

                # Prepend context for the first message in a session to avoid an extra turn
                prompt_text = f"{context_msg}\n\n{message}" if is_new_session else message

                # Catch-up Hinting: Compare messages in thread vs processed count
                # thread_counters is ensured to exist and be accurate by _process_post
                thread_size = self.thread_counters.get(root_id, 1)
                processed_count = session_data.get("processed_count", 0)
                # New messages are those in the thread excluding the one we are currently processing
                new_messages_count = max(0, thread_size - processed_count - 1)

                had_hint = session_data.get("had_catchup_hint", False)
                if new_messages_count > 0:
                    if is_new_session:
                        hint = f"SYSTEM: You have joined an existing thread with {new_messages_count} earlier messages. Use your tools if you need to catch up on the history."
                    else:
                        hint = f"SYSTEM: There are {new_messages_count} new messages in this thread since your last response. Use your tools if you need to catch up."

                    if self.config.debug:
                        print(
                            f"[{datetime.now()}] Merging catch-up hint for {session_key}: {new_messages_count} new messages"
                        )
                    prompt_text = f"{hint}\n\n{prompt_text}"
                    session_data["had_catchup_hint"] = True
                elif had_hint:
                    # If we had a hint before but now have 0 new messages, explicitly clear the state to avoid confusion
                    prompt_text = f"SYSTEM: You are now caught up with the thread.\n\n{prompt_text}"
                    session_data["had_catchup_hint"] = False

                try:
                    await self._stream_response_to_mattermost(
                        client, goose_sid, prompt_text, channel_id, root_id)
                    # Update processed count to the current thread size (user messages handled)
                    session_data["processed_count"] = thread_size
                except (ValueError, RuntimeError, asyncio.TimeoutError) as e:
                    print(
                        f"[{datetime.now()}] Session {session_key} lost, retrying once: {e}"
                    )
                    agent_display = "Goose" if agent_name == "goose" else f"Agent '{agent_name}'"
                    await self.api.create_post(
                        channel_id,
                        f"🔄 *Notice: Connection to {agent_display} was reset. I am starting a fresh session for this thread.*",
                        root_id=root_id,
                    )
                    self.sessions[session_key] = {
                        "id": await client.create_session(),
                        "linux_user": linux_user,
                        "agent_name": agent_name,
                        "processed_count": 0,
                        "had_catchup_hint": False
                    }
                    goose_sid = self.sessions[session_key]["id"]
                    # Also prepend context for the fresh retry session
                    retry_context = f"{context_msg} NOTE: The previous session for this thread terminated unexpectedly. Context from earlier in this conversation has been lost, but you can use the IDs above to try to recover history if needed."
                    await self._stream_response_to_mattermost(
                        client, goose_sid, f"{retry_context}\n\n{message}",
                        channel_id, root_id)

            except Exception as e:
                print(
                    f"[{datetime.now()}] Error handling message for {session_key}: {e}"
                )
                await self.api.create_post(
                    channel_id,
                    f"⚠️ Sorry, I encountered an error: {str(e)}",
                    root_id=root_id)
            finally:
                if self.active_tasks.get(session_key) == asyncio.current_task():
                    del self.active_tasks[session_key]

    async def _process_post(self, post: dict, channel_map: dict):
        """Processes a single post from Mattermost."""
        sender_id = post["user_id"]
        if sender_id == self.bot_id:
            return

        message = post.get("message", "").strip()
        if not message:
            return

        cid = post["channel_id"]
        channel = channel_map.get(cid)
        is_dm = channel and channel.get("type") == "D"
        is_mentioned = self.bot_mention in message
        root_id = post.get("root_id") or post["id"]

        # Update thread counter:
        # If we are already tracking this thread, increment it.
        if root_id in self.thread_counters:
            self.thread_counters[root_id] += 1

        cleaned_msg = clean_message(message, self.bot_mention)

        # Special Command: !impersonate
        if cleaned_msg.lower().startswith("!impersonate"):
            await self._handle_impersonate_command(post, cleaned_msg)
            return

        # Special Command: !stop
        if message.lower() == "!stop":
            await self._handle_stop_command(post)
            return

        # Special Command: !context
        if message.lower() == "!context":
            await self._handle_context_command(post)
            return

        # Special Command: !agents
        if cleaned_msg.strip().lower() == "!agents":
            await self._handle_agents_command(post)
            return

        # Special Command: !agent-default
        if cleaned_msg.lower().startswith("!agent-default"):
            await self._handle_agent_default_command(post, cleaned_msg)
            return

        # Special Command: !agent
        if cleaned_msg.lower().startswith("!agent"):
            await self._handle_agent_command(post, cleaned_msg)
            return

        # Check if we should respond
        if not is_dm and not is_mentioned:
            return

        # If we are starting to respond but don't have a counter yet, prime it.
        # This baseline includes all messages in the thread (including this one).
        if root_id not in self.thread_counters:
            thread_data = await self.api.get_thread(root_id)
            if thread_data and "posts" in thread_data:
                # Baseline includes all other users' posts up to and including this one.
                others_posts = [
                    p for p in thread_data["posts"].values()
                    if p.get("user_id") != self.bot_id and
                    p.get("message", "").strip()
                ]
                self.thread_counters[root_id] = len(others_posts)
            else:
                self.thread_counters[root_id] = 1

        user_info = await self.api.get_user(sender_id)
        username = user_info.get("username") if user_info else "unknown"

        if self.config.approved_users:
            if sender_id not in self.config.approved_users and username not in self.config.approved_users:
                if self.config.debug:
                    print(
                        f"[{datetime.now()}] Ignoring message from unapproved user: {username} ({sender_id})"
                    )
                return

        # Resolve effective sender (impersonation lookup)
        eff_id = sender_id
        eff_username = username
        if sender_id in self.impersonations:
            eff_id = self.impersonations[sender_id]["id"]
            eff_username = self.impersonations[sender_id]["username"]

        # Linux User Mapping
        user_mapping = load_user_mapping(self.config.user_mapping_file)
        linux_user = user_mapping.get(eff_id) or user_mapping.get(eff_username)

        if self.config.require_user_mapping and not linux_user:
            print(
                f"[{datetime.now()}] Rejecting approved user {eff_username}: No Linux user mapping and REQUIRE_USER_MAPPING=true"
            )
            await self.api.create_post(
                cid,
                f"⚠️ Your account is approved but has no assigned OS-level isolation profile. Please contact an administrator.",
                root_id=post.get("root_id") or post["id"],
            )
            return

        # Spawn task to handle message
        task = asyncio.create_task(
            self._handle_message(post,
                                 linux_user,
                                 eff_username,
                                 sender_id=eff_id))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def run(self):
        """Main loop for polling Mattermost and handling messages."""
        if not await self.initialize():
            return

        print(
            f"[{datetime.now()}] Bridge is polling for messages. Press Ctrl+C to stop."
        )

        try:
            while True:
                try:
                    await self._update_channel_cache()
                    channel_map = {c["id"]: c for c in self.channels_cache}

                    new_since = self.last_since
                    for cid in channel_map.keys():
                        posts_data = await self.api.get_channel_posts(
                            cid, self.last_since)
                        if not posts_data or "posts" not in posts_data:
                            continue

                        sorted_posts = sorted(posts_data["posts"].values(),
                                              key=lambda x: x["create_at"])

                        for post in sorted_posts:
                            if post["create_at"] <= self.last_since:
                                continue
                            new_since = max(new_since, post["create_at"])
                            await self._process_post(post, channel_map)

                    await self._prune_sessions()
                    self.last_since = new_since
                    await asyncio.sleep(self.config.poll_interval)

                except Exception as e:
                    print(f"[{datetime.now()}] Bridge Loop Error: {e}")
                    await asyncio.sleep(5)
        except KeyboardInterrupt:
            pass
        finally:
            print(f"[{datetime.now()}] Shutting down bridge...")
            # Cancel all background tasks
            for task in self.background_tasks:
                task.cancel()
            if self.background_tasks:
                await asyncio.gather(*self.background_tasks,
                                     return_exceptions=True)

            # Close all agent clients
            for client in self.agent_clients.values():
                if client.process and client.process.returncode is None:
                    try:
                        client.process.terminate()
                    except:
                        pass
