import asyncio
import time
from datetime import datetime
from typing import Dict

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
from utils import clean_message, load_user_mapping


async def handle_message(
    api: MattermostAPI,
    goose_clients: Dict[str, GooseACPClient],
    linux_user: str,
    post: dict,
    sessions: dict,
    session_locks: dict,
    active_tasks: dict,
    bot_mention: str,
):
    """Handles an incoming message from Mattermost."""
    sender_id = post["user_id"]
    message = post.get("message", "").strip()
    if not message:
        return
    channel_id = post["channel_id"]
    root_id = post.get("root_id") or post["id"]
    session_key = f"{sender_id}:{root_id}"

    # Register this task so it can be interrupted
    active_tasks[session_key] = asyncio.current_task()

    # Use a lock per session to ensure messages in the same thread are processed in order
    if session_key not in session_locks:
        session_locks[session_key] = asyncio.Lock()

    if linux_user not in goose_clients:
        goose_clients[linux_user] = GooseACPClient(linux_user)
    goose = goose_clients[linux_user]

    async with session_locks[session_key]:
        try:
            message = clean_message(message, bot_mention)

            if session_key not in sessions:
                print(f"[{datetime.now()}] Creating new Goose session for {session_key}")
                sessions[session_key] = {
                    "id": await goose.create_session(),
                    "linux_user": linux_user,
                }

            session_data = sessions[session_key]
            goose_sid = session_data["id"]
            print(f"[{datetime.now()}] User {sender_id} says: {message[:100]}...")

            async def run_prompt(sid, msg):
                thinking_post = None
                full_response = ""
                thinking_trace = ""
                last_update_time = 0

                async for update in goose.prompt(sid, msg):
                    if update["type"] == "thinking":
                        thinking_trace += update["text"]
                    elif update["type"] == "tool":
                        thinking_trace += f"\n\n**Using tool**: `{update['name']}`\n"

                    # Truncate thinking trace if it gets too large for Mattermost
                    if len(thinking_trace) > 10000:
                        thinking_trace = (
                            "... (truncated) ...\n" + thinking_trace[-8000:]
                        )
                    elif update["type"] == "content":
                        full_response = update["text"]
                    elif update["type"] == "final":
                        full_response = update["text"]

                    current_time = time.time()
                    should_update = False
                    if update["type"] == "final":
                        should_update = True
                    elif (
                        GOOSE_THINKING_TRACE
                        and thinking_trace
                        and current_time - last_update_time > 1.0
                    ):
                        should_update = True

                    if should_update:
                        resp_msg = ""
                        props = {}
                        if update["type"] != "final":
                            resp_msg = ":thinking_face: **Thinking...**"
                            props = {
                                "attachments": [
                                    {
                                        "text": thinking_trace,
                                        "title": "Thinking Trace",
                                        "color": "#9b9b9b",
                                    }
                                ]
                            }
                        else:
                            resp_msg = full_response
                            if GOOSE_THINKING_TRACE and thinking_trace:
                                props = {
                                    "attachments": [
                                        {
                                            "text": thinking_trace,
                                            "title": "Thinking Trace",
                                            "color": "#9b9b9b",
                                        }
                                    ]
                                }

                        if not thinking_post:
                            thinking_post = await api.create_post(
                                channel_id, resp_msg, root_id=root_id, props=props
                            )
                        else:
                            await api.update_post(
                                thinking_post["id"], resp_msg, props=props
                            )
                        last_update_time = current_time

            try:
                await run_prompt(goose_sid, message)
            except (ValueError, RuntimeError, asyncio.TimeoutError) as e:
                # Session was likely lost due to a restart
                print(f"[{datetime.now()}] Session {session_key} lost, retrying once: {e}")
                await api.create_post(
                    channel_id,
                    "🔄 *Notice: Connection to Goose was reset. I am starting a fresh session for this thread.*",
                    root_id=root_id,
                )
                sessions[session_key] = {
                    "id": await goose.create_session(),
                    "linux_user": linux_user,
                }
                goose_sid = sessions[session_key]["id"]
                await run_prompt(goose_sid, message)

        except Exception as e:
            print(f"[{datetime.now()}] Error handling message for {session_key}: {e}")
            await api.create_post(
                channel_id,
                f"⚠️ Sorry, I encountered an error: {str(e)}",
                root_id=root_id,
            )
        finally:
            if active_tasks.get(session_key) == asyncio.current_task():
                del active_tasks[session_key]


async def run_bridge():
    """Main bridge loop for polling Mattermost and handling messages."""
    api = MattermostAPI()
    # Map linux_user -> GooseACPClient
    goose_clients: Dict[str, GooseACPClient] = {}

    me = await api.get_me()
    if not me:
        print(
            f"[{datetime.now()}] Failed to connect to Mattermost. Check your URL and TOKEN."
        )
        return

    bot_id = me["id"]
    bot_username = me["username"]
    bot_mention = f"@{bot_username}"
    print(f"[{datetime.now()}] Connected as {bot_username} ({bot_id})")

    user_mapping = load_user_mapping()
    if not user_mapping:
        print(
            f"[{datetime.now()}] WARNING: No user mapping found. Bridge will run as current user for all requests."
        )

    # Track sessions: key = user_id:root_id, value = goose_session_id
    sessions = {}
    active_tasks = {}
    session_locks = {}

    # Caching for Mattermost channels to reduce API load
    channels_cache = []
    last_cache_update = 0
    CACHE_TTL = 60  # Update cache every 60 seconds

    last_since = int(time.time() * 1000)
    print(f"[{datetime.now()}] Bridge is polling for messages. Press Ctrl+C to stop.")

    while True:
        try:
            current_time = time.time()
            if not channels_cache or current_time - last_cache_update > CACHE_TTL:
                if DEBUG:
                    print(f"[{datetime.now()}] Updating Mattermost channels cache...")
                # Get channels to check
                channels = await api.get_direct_channels() or []
                teams = await api.get_my_teams() or []
                for team in teams:
                    team_channels = await api.get_my_channels(team["id"]) or []
                    channels.extend(team_channels)

                # De-duplicate channels and store
                channels_cache = list({c["id"]: c for c in channels}.values())
                last_cache_update = current_time

            channel_map = {c["id"]: c for c in channels_cache}
            channel_ids = set(channel_map.keys())

            new_since = last_since
            for cid in channel_ids:
                posts_data = await api.get_channel_posts(cid, last_since)
                if not posts_data or "posts" not in posts_data:
                    continue

                # Sort posts by creation time
                sorted_posts = sorted(
                    posts_data["posts"].values(), key=lambda x: x["create_at"]
                )

                channel = channel_map.get(cid)
                is_dm = channel and channel.get("type") == "D"

                for post in sorted_posts:
                    if post["create_at"] <= last_since:
                        continue

                    new_since = max(new_since, post["create_at"])
                    sender_id = post["user_id"]

                    if sender_id == bot_id:
                        continue

                    message = post.get("message", "").strip()
                    if not message:
                        continue

                    # User Identity & Approval Check
                    user_info = await api.get_user(sender_id)
                    username = user_info.get("username") if user_info else "unknown"

                    # Special Command: !stop
                    # This check happens before mention check so users can stop without mentioning
                    if message.lower() == "!stop":
                        root_id = post.get("root_id") or post["id"]
                        session_key = f"{sender_id}:{root_id}"
                        
                        interrupted = False
                        if session_key in sessions:
                            print(f"[{datetime.now()}] Interruption requested for {session_key}")
                            sid = sessions[session_key]["id"]
                            # Reload mapping for current user
                            user_mapping = load_user_mapping()
                            linux_user = user_mapping.get(sender_id) or user_mapping.get(username)
                            
                            if linux_user and linux_user in goose_clients:
                                await goose_clients[linux_user].cancel_prompt(sid)
                                interrupted = True
                        
                        if session_key in active_tasks:
                            active_tasks[session_key].cancel()
                            interrupted = True
                            
                        if interrupted:
                            await api.create_post(
                                cid,
                                "🛑 *Prompt cancelled.*",
                                root_id=root_id,
                            )
                        continue

                    # Check if we should respond
                    is_mentioned = bot_mention in message

                    if not is_dm and not is_mentioned:
                        continue

                    if APPROVED_USERS:
                        if (
                            sender_id not in APPROVED_USERS
                            and username not in APPROVED_USERS
                        ):
                            if DEBUG:
                                print(
                                    f"[{datetime.now()}] Ignoring message from unapproved user: {username} ({sender_id})"
                                )
                            continue

                    # Linux User Mapping
                    user_mapping = load_user_mapping()  # Reload to pick up changes
                    linux_user = user_mapping.get(sender_id) or user_mapping.get(
                        username
                    )

                    if REQUIRE_USER_MAPPING and not linux_user:
                        print(
                            f"[{datetime.now()}] Rejecting approved user {username}: No Linux user mapping and REQUIRE_USER_MAPPING=true"
                        )
                        await api.create_post(
                            cid,
                            f"⚠️ Your account is approved but has no assigned OS-level isolation profile. Please contact an administrator.",
                            root_id=post.get("root_id") or post["id"],
                        )
                        continue

                    # Spawn task to handle message
                    asyncio.create_task(
                        handle_message(
                            api,
                            goose_clients,
                            linux_user,
                            post,
                            sessions,
                            session_locks,
                            active_tasks,
                            bot_mention,
                        )
                    )

            # Prune old sessions if there are too many
            if len(sessions) > MAX_SESSIONS:
                # Simple FIFO pruning: remove the first 20% of keys
                prune_count = max(1, MAX_SESSIONS // 5)
                keys_to_remove = list(sessions.keys())[:prune_count]
                for k in keys_to_remove:
                    session_data = sessions.pop(k)
                    sid = session_data["id"]
                    target_linux_user = session_data["linux_user"]
                    if DEBUG:
                        print(f"DEBUG: Pruning old session for {k} ({sid})")

                    if target_linux_user in goose_clients:
                        client = goose_clients[target_linux_user]
                        if sid in client.session_queues:
                            del client.session_queues[sid]
                        asyncio.create_task(
                            client.send_request("session/close", {"sessionId": sid})
                        )

                    if k in session_locks:
                        del session_locks[k]

            last_since = new_since
            await asyncio.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[{datetime.now()}] Bridge Loop Error: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(run_bridge())
    except KeyboardInterrupt:
        print("\nShutting down...")