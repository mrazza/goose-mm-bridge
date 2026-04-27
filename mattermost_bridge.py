import asyncio
import time
from datetime import datetime
from typing import Dict, List, Optional

from config import (
    APPROVED_USERS,
    DEBUG,
    GOOSE_THINKING_TRACE,
    MAX_SESSIONS,
    POLL_INTERVAL,
    REQUIRE_USER_MAPPING,
)
from goose_acp_client import GooseACPClient
from mattermost_api import MattermostAPI
from utils import clean_message, load_user_mapping, get_session_key

CACHE_TTL = 60  # Update cache every 60 seconds


class MattermostBridge:
    """Manages the connection between Mattermost and Goose."""

    def __init__(self):
        self.api = MattermostAPI()
        self.goose_clients: Dict[str, GooseACPClient] = {}
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

    async def initialize(self) -> bool:
        """Initializes the bridge by connecting to Mattermost."""
        me = await self.api.get_me()
        if not me:
            print(f"[{datetime.now()}] Failed to connect to Mattermost. Check your URL and TOKEN.")
            return False

        self.bot_id = me["id"]
        self.bot_username = me["username"]
        self.bot_mention = f"@{self.bot_username}"
        print(f"[{datetime.now()}] Connected as {self.bot_username} ({self.bot_id})")

        user_mapping = load_user_mapping()
        if not user_mapping:
            print(f"[{datetime.now()}] WARNING: No user mapping found. Bridge will run as current user for all requests.")
        
        return True

    async def _update_channel_cache(self):
        """Updates the internal cache of Mattermost channels."""
        current_time = time.time()
        if not self.channels_cache or current_time - self.last_cache_update > CACHE_TTL:
            if DEBUG:
                print(f"[{datetime.now()}] Updating Mattermost channels cache...")
            
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
        session_key = get_session_key(sender_id, root_id)
        
        interrupted = False
        if session_key in self.sessions:
            print(f"[{datetime.now()}] Interruption requested for {session_key}")
            sid = self.sessions[session_key]["id"]
            user_mapping = load_user_mapping()
            user_info = await self.api.get_user(sender_id)
            username = user_info.get("username") if user_info else "unknown"
            linux_user = user_mapping.get(sender_id) or user_mapping.get(username)
            
            if linux_user and linux_user in self.goose_clients:
                await self.goose_clients[linux_user].cancel_prompt(sid)
                interrupted = True
        
        if session_key in self.active_tasks:
            self.active_tasks[session_key].cancel()
            interrupted = True
            
        if interrupted:
            await self.api.create_post(cid, "🛑 *Prompt cancelled.*", root_id=root_id)

    async def _prune_sessions(self):
        """Prunes old sessions if the count exceeds MAX_SESSIONS."""
        if len(self.sessions) <= MAX_SESSIONS:
            return

        prune_count = max(1, MAX_SESSIONS // 5)
        keys_to_remove = list(self.sessions.keys())[:prune_count]
        for k in keys_to_remove:
            session_data = self.sessions.pop(k)
            sid = session_data["id"]
            target_linux_user = session_data["linux_user"]
            if DEBUG:
                print(f"DEBUG: Pruning old session for {k} ({sid})")

            if target_linux_user in self.goose_clients:
                client = self.goose_clients[target_linux_user]
                if sid in client.session_queues:
                    del client.session_queues[sid]
                asyncio.create_task(client.send_request("session/close", {"sessionId": sid}))

            if k in self.session_locks:
                del self.session_locks[k]

    async def _stream_response_to_mattermost(self, goose: GooseACPClient, sid: str, msg: str, channel_id: str, root_id: str):
        """Streams a response from Goose to Mattermost."""
        thinking_post = None
        full_response = ""
        thinking_trace = ""
        last_update_time = 0

        async for update in goose.prompt(sid, msg):
            if update["type"] == "thinking":
                thinking_trace += update["text"]
            elif update["type"] == "tool":
                thinking_trace += f"\n\n**Using tool**: `{update['name']}`\n"

            if len(thinking_trace) > 10000:
                thinking_trace = "... (truncated) ...\n" + thinking_trace[-8000:]
            elif update["type"] == "content":
                full_response = update["text"]
            elif update["type"] == "final":
                full_response = update["text"]

            current_time = time.time()
            should_update = False
            if update["type"] == "final":
                should_update = True
            elif (GOOSE_THINKING_TRACE and thinking_trace and current_time - last_update_time > 1.0):
                should_update = True

            if should_update:
                resp_msg = ""
                props = {}
                if update["type"] != "final":
                    resp_msg = ":thinking_face: **Thinking...**"
                    props = {"attachments": [{"text": thinking_trace, "title": "Thinking Trace", "color": "#9b9b9b"}]}
                else:
                    resp_msg = full_response
                    if GOOSE_THINKING_TRACE and thinking_trace:
                        props = {"attachments": [{"text": thinking_trace, "title": "Thinking Trace", "color": "#9b9b9b"}]}

                if not thinking_post:
                    thinking_post = await self.api.create_post(channel_id, resp_msg, root_id=root_id, props=props)
                else:
                    await self.api.update_post(thinking_post["id"], resp_msg, props=props)
                last_update_time = current_time

    async def _handle_message(self, post: dict, linux_user: Optional[str]):
        """Handles an incoming message from Mattermost."""
        sender_id = post["user_id"]
        message = post.get("message", "").strip()
        if not message:
            return
            
        channel_id = post["channel_id"]
        root_id = post.get("root_id") or post["id"]
        session_key = get_session_key(sender_id, root_id)

        self.active_tasks[session_key] = asyncio.current_task()

        if session_key not in self.session_locks:
            self.session_locks[session_key] = asyncio.Lock()

        if linux_user not in self.goose_clients:
            self.goose_clients[linux_user] = GooseACPClient(linux_user)
        goose = self.goose_clients[linux_user]

        async with self.session_locks[session_key]:
            try:
                message = clean_message(message, self.bot_mention)

                if session_key not in self.sessions:
                    print(f"[{datetime.now()}] Creating new Goose session for {session_key}")
                    self.sessions[session_key] = {
                        "id": await goose.create_session(),
                        "linux_user": linux_user,
                    }

                session_data = self.sessions[session_key]
                goose_sid = session_data["id"]
                print(f"[{datetime.now()}] User {sender_id} says: {message[:100]}...")

                try:
                    await self._stream_response_to_mattermost(goose, goose_sid, message, channel_id, root_id)
                except (ValueError, RuntimeError, asyncio.TimeoutError) as e:
                    print(f"[{datetime.now()}] Session {session_key} lost, retrying once: {e}")
                    await self.api.create_post(
                        channel_id,
                        "🔄 *Notice: Connection to Goose was reset. I am starting a fresh session for this thread.*",
                        root_id=root_id,
                    )
                    self.sessions[session_key] = {
                        "id": await goose.create_session(),
                        "linux_user": linux_user,
                    }
                    goose_sid = self.sessions[session_key]["id"]
                    await self._stream_response_to_mattermost(goose, goose_sid, message, channel_id, root_id)

            except Exception as e:
                print(f"[{datetime.now()}] Error handling message for {session_key}: {e}")
                await self.api.create_post(channel_id, f"⚠️ Sorry, I encountered an error: {str(e)}", root_id=root_id)
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

        # Special Command: !stop
        if message.lower() == "!stop":
            await self._handle_stop_command(post)
            return

        # Check if we should respond
        is_mentioned = self.bot_mention in message
        if not is_dm and not is_mentioned:
            return

        user_info = await self.api.get_user(sender_id)
        username = user_info.get("username") if user_info else "unknown"

        if APPROVED_USERS:
            if sender_id not in APPROVED_USERS and username not in APPROVED_USERS:
                if DEBUG:
                    print(f"[{datetime.now()}] Ignoring message from unapproved user: {username} ({sender_id})")
                return

        # Linux User Mapping
        user_mapping = load_user_mapping()
        linux_user = user_mapping.get(sender_id) or user_mapping.get(username)

        if REQUIRE_USER_MAPPING and not linux_user:
            print(f"[{datetime.now()}] Rejecting approved user {username}: No Linux user mapping and REQUIRE_USER_MAPPING=true")
            await self.api.create_post(
                cid,
                f"⚠️ Your account is approved but has no assigned OS-level isolation profile. Please contact an administrator.",
                root_id=post.get("root_id") or post["id"],
            )
            return

        # Spawn task to handle message
        task = asyncio.create_task(self._handle_message(post, linux_user))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def run(self):
        """Main loop for polling Mattermost and handling messages."""
        if not await self.initialize():
            return

        print(f"[{datetime.now()}] Bridge is polling for messages. Press Ctrl+C to stop.")

        try:
            while True:
                try:
                    await self._update_channel_cache()
                    channel_map = {c["id"]: c for c in self.channels_cache}
                    
                    new_since = self.last_since
                    for cid in channel_map.keys():
                        posts_data = await self.api.get_channel_posts(cid, self.last_since)
                        if not posts_data or "posts" not in posts_data:
                            continue

                        sorted_posts = sorted(posts_data["posts"].values(), key=lambda x: x["create_at"])

                        for post in sorted_posts:
                            if post["create_at"] <= self.last_since:
                                continue
                            new_since = max(new_since, post["create_at"])
                            await self._process_post(post, channel_map)

                    await self._prune_sessions()
                    self.last_since = new_since
                    await asyncio.sleep(POLL_INTERVAL)

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
                await asyncio.gather(*self.background_tasks, return_exceptions=True)
            
            # Close all goose clients
            for client in self.goose_clients.values():
                if client.process and client.process.returncode is None:
                    try:
                        client.process.terminate()
                    except:
                        pass
