import asyncio
import json
import os
import sys
import time
import ssl
import urllib.request
import urllib.error
from datetime import datetime
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv

load_dotenv()

# Configuration from environment
MATTERMOST_URL = os.getenv("MATTERMOST_URL", "").strip().rstrip('/')
MATTERMOST_TOKEN = os.getenv("MATTERMOST_TOKEN")
MATTERMOST_SCHEME = os.getenv("MATTERMOST_SCHEME", "https")
MATTERMOST_PORT = os.getenv("MATTERMOST_PORT", "443")
APPROVED_USERS = [u.strip() for u in os.getenv("APPROVED_USERS", "").split(",") if u.strip()]
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "1"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
GOOSE_THINKING_TRACE = os.getenv("GOOSE_THINKING_TRACE", "true").lower() == "true"
RPC_TIMEOUT = int(os.getenv("RPC_TIMEOUT", "60"))

class GooseACPClient:
    def __init__(self):
        self.process = None
        self.message_id = 1
        self.pending_requests: Dict[int, asyncio.Future] = {}
        self.session_queues: Dict[str, asyncio.Queue] = {}
        self._start_lock = asyncio.Lock()

    async def ensure_running(self):
        async with self._start_lock:
            if self.process is None or self.process.returncode is not None:
                if self.process is not None:
                    print(f"[{datetime.now()}] Goose ACP process died (code {self.process.returncode}). Restarting...")
                    # Fail any pending requests
                    for fut in self.pending_requests.values():
                        if not fut.done():
                            fut.set_exception(RuntimeError("Goose ACP process terminated"))
                    self.pending_requests.clear()
                    # Clear session queues as they are tied to the old process
                    self.session_queues.clear()
                
                await self._start()

    async def _start(self):
        print(f"[{datetime.now()}] Starting Goose ACP process...")
        self.process = await asyncio.create_subprocess_exec(
            "goose", "acp",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())
        
        # Handshake
        try:
            # Note: we use _send_raw_request here because send_request calls ensure_running
            await asyncio.wait_for(self._send_raw_request("initialize", {
                "protocolVersion": 0,
                "capabilities": {},
                "clientInfo": {"name": "goose-mm-bridge", "version": "1.0.0"}
            }), timeout=RPC_TIMEOUT)
            print(f"[{datetime.now()}] Goose ACP initialized.")
        except Exception as e:
            print(f"[{datetime.now()}] Failed to initialize Goose ACP: {e}")
            if self.process:
                try:
                    self.process.terminate()
                except:
                    pass
                self.process = None
            raise

    async def _read_stdout(self):
        while True:
            if self.process is None or self.process.stdout.at_eof():
                break
                
            line = await self.process.stdout.readline()
            if not line:
                break
            
            try:
                line_str = line.decode().strip()
                if not line_str: continue
                if DEBUG:
                    print(f"DEBUG: GOOSE -> BRIDGE: {line_str}")
                res = json.loads(line_str)
                req_id = res.get("id")
                
                if req_id is not None and req_id in self.pending_requests:
                    if not self.pending_requests[req_id].done():
                        self.pending_requests[req_id].set_result(res)
                    del self.pending_requests[req_id]
                
                if res.get("method") in ["session/prompt/next", "session/update"]:
                    params = res.get("params", {})
                    session_id = params.get("sessionId")
                    if session_id and session_id in self.session_queues:
                        await self.session_queues[session_id].put(res)
            except Exception as e:
                print(f"Error parsing Goose output: {e}")
        
        # Fail any remaining pending requests
        for fut in list(self.pending_requests.values()):
            if not fut.done():
                fut.set_exception(RuntimeError("Goose ACP stdout closed"))
        self.pending_requests.clear()
        print(f"[{datetime.now()}] Goose ACP stdout closed.")

    async def _read_stderr(self):
        while True:
            if self.process is None or self.process.stderr.at_eof():
                break
            line = await self.process.stderr.readline()
            if not line:
                break
            msg = line.decode().strip()
            if msg:
                print(f"[GOOSE-STDERR] {msg}", file=sys.stderr)

    async def _send_raw_request(self, method: str, params: dict = None) -> dict:
        req_id = self.message_id
        self.message_id += 1
        
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": req_id
        }
        
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending_requests[req_id] = future
        
        self.process.stdin.write((json.dumps(request) + "\n").encode())
        await self.process.stdin.drain()
        
        return await future

    async def send_request(self, method: str, params: dict = None) -> dict:
        await self.ensure_running()
        if DEBUG:
            print(f"DEBUG: BRIDGE -> GOOSE: {method}({params})")
        
        try:
            return await asyncio.wait_for(self._send_raw_request(method, params), timeout=RPC_TIMEOUT)
        except asyncio.TimeoutError:
            print(f"[{datetime.now()}] Request {method} timed out after {RPC_TIMEOUT}s")
            raise

    async def create_session(self) -> str:
        res = await self.send_request("session/new", {
            "cwd": os.getcwd(),
            "mcpServers": []
        })
        if "error" in res:
            raise Exception(f"Failed to create session: {res['error']}")
        session_id = res["result"]["sessionId"]
        self.session_queues[session_id] = asyncio.Queue()
        return session_id

    async def prompt(self, session_id: str, text: str):
        if DEBUG:
            print(f"DEBUG: Starting prompt for session {session_id}")
        
        if session_id not in self.session_queues:
            # Session might have been lost due to a restart
            raise ValueError(f"Session {session_id} not found (may have been reset)")

        # Clear existing chunks
        while not self.session_queues[session_id].empty():
            self.session_queues[session_id].get_nowait()
            
        res_future = asyncio.create_task(self.send_request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}]
        }))
        
        full_response = ""
        while True:
            try:
                # Wait for a chunk or the final response
                chunk_task = asyncio.create_task(self.session_queues[session_id].get())
                done, pending = await asyncio.wait(
                    [chunk_task, res_future], 
                    timeout=0.1,
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                if chunk_task in done:
                    chunk = chunk_task.result()
                    if DEBUG:
                        print(f"DEBUG: Chunk for {session_id}: {chunk}")
                    params = chunk.get("params", {})
                    update = params.get("update", {})
                    
                    # Handle session/prompt/next format
                    if params.get("chunk", {}).get("type") == "text":
                        full_response += params["chunk"]["text"]
                        yield {"type": "content", "text": full_response}
                    
                    # Handle session/update format
                    session_update = update.get("sessionUpdate")
                    if session_update == "agent_message_chunk":
                        content_obj = update.get("content", {})
                        if content_obj.get("type") == "text":
                            full_response += content_obj.get("text", "")
                            yield {"type": "content", "text": full_response}
                    elif session_update == "agent_thinking_chunk":
                        yield {"type": "thinking", "text": update.get("thinking", "")}
                    elif session_update == "call_tool":
                        tool_call = update.get("toolCall", {})
                        yield {"type": "tool", "name": tool_call.get("name"), "arguments": tool_call.get("arguments")}
                    elif session_update == "tool_call":
                        yield {"type": "tool", "name": update.get("title") or "tool", "arguments": {}}
                    elif session_update == "tool_call_update":
                        title = update.get("title")
                        if title:
                            yield {"type": "thinking", "text": f"\n**Updated**: `{title}`\n"}
                        
                elif not chunk_task.done():
                    chunk_task.cancel()

                if res_future in done:
                    if DEBUG:
                        print(f"DEBUG: Final result for {session_id}")
                    # Final result received, but there might be more chunks in the queue
                    res = res_future.result()
                    if "error" in res:
                         raise Exception(f"Goose error: {res['error']}")
                         
                    while not self.session_queues[session_id].empty():
                        chunk = await self.session_queues[session_id].get()
                        if DEBUG:
                            print(f"DEBUG: Draining late chunk for {session_id}")
                        
                        params = chunk.get("params", {})
                        # Handle session/prompt/next
                        if params.get("chunk", {}).get("type") == "text":
                            full_response += params["chunk"]["text"]
                        
                        # Handle session/update
                        update = params.get("update", {})
                        if update.get("sessionUpdate") == "agent_message_chunk":
                            content_obj = update.get("content", {})
                            if content_obj.get("type") == "text":
                                full_response += content_obj.get("text", "")
                    break
                    
                # If neither is done, check if process is still alive
                if self.process is None or self.process.returncode is not None:
                    raise RuntimeError("Goose ACP process terminated during prompt")
                    
            except Exception as e:
                if not res_future.done():
                    res_future.cancel()
                raise
        
        yield {"type": "final", "text": full_response}

class MattermostAPI:
    def __init__(self):
        self.base_url = f"{MATTERMOST_SCHEME}://{MATTERMOST_URL}:{MATTERMOST_PORT}/api/v4"
        self.token = MATTERMOST_TOKEN
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        # Create SSL context that ignores cert issues if needed
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    async def _request(self, path, data=None, method="GET"):
        return await asyncio.to_thread(self._sync_request, path, data, method)

    def _sync_request(self, path, data, method):
        url = f"{self.base_url}{path}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=self.headers, method=method)
        try:
            # Using our custom SSL context for all requests
            with urllib.request.urlopen(req, context=self.ssl_context) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            if e.code != 404:
                print(f"[{datetime.now()}] MM API Error ({method} {path}): {e.code} {e.reason}")
            return None
        except Exception as e:
            print(f"[{datetime.now()}] MM Request Error ({method} {path}): {e}")
            return None

    def get_me(self):
        return await self._request("/users/me")

    def get_direct_channels(self):
        return await self._request("/users/me/channels")

    def get_my_teams(self):
        return await self._request("/users/me/teams")

    def get_my_channels(self, team_id):
        return self._request(f"/users/me/teams/{team_id}/channels")

    def get_channel_posts(self, channel_id, since):
        return self._request(f"/channels/{channel_id}/posts?since={since}")

    def create_post(self, channel_id, message, root_id=None, props=None):
        data = {"channel_id": channel_id, "message": message, "root_id": root_id}
        if props:
            data["props"] = props
        return await self._request("/posts", data=data, method="POST")

    def update_post(self, post_id, message, props=None):
        data = {"id": post_id, "message": message}
        if props:
            data["props"] = props
        return self._request(f"/posts/{post_id}", data=data, method="PUT")


def clean_message(message: str, bot_mention: str) -> str:
    if bot_mention in message:
        message = message.replace(bot_mention, "").strip()
        if message.startswith(",") or message.startswith(":"):
            message = message[1:].strip()
    return message

async def handle_message(api: MattermostAPI, goose: GooseACPClient, post: dict, sessions: dict, session_locks: dict, bot_mention: str):
    sender_id = post["user_id"]
    message = post.get("message", "").strip()
    channel_id = post["channel_id"]
    root_id = post.get("root_id") or post["id"]
    session_key = f"{sender_id}:{root_id}"

    # Use a lock per session to ensure messages in the same thread are processed in order
    if session_key not in session_locks:
        session_locks[session_key] = asyncio.Lock()

    async with session_locks[session_key]:
        try:
            message = clean_message(message, bot_mention)

            if session_key not in sessions:
                print(f"[{datetime.now()}] Creating new Goose session for {session_key}")
                sessions[session_key] = await goose.create_session()
            
            goose_sid = sessions[session_key]
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
                    elif update["type"] == "content":
                        full_response = update["text"]
                    elif update["type"] == "final":
                        full_response = update["text"]

                    current_time = time.time()
                    should_update = False
                    if update["type"] == "final":
                        should_update = True
                    elif GOOSE_THINKING_TRACE and thinking_trace and current_time - last_update_time > 1.0:
                        should_update = True
                        
                    if should_update:
                        resp_msg = ""
                        props = {}
                        if update["type"] != "final":
                            resp_msg = f":thinking_face: **Thinking...**"
                            props = {"attachments": [{"text": thinking_trace, "title": "Thinking Trace", "color": "#9b9b9b"}]}
                        else:
                            resp_msg = full_response
                            if GOOSE_THINKING_TRACE and thinking_trace:
                                props = {"attachments": [{"text": thinking_trace, "title": "Thinking Trace", "color": "#9b9b9b"}]}
                        
                        if not thinking_post:
                            thinking_post = await api.create_post(channel_id, resp_msg, root_id=root_id, props=props)
                        else:
                            await api.update_post(thinking_post["id"], resp_msg, props=props)
                        last_update_time = current_time

            try:
                await run_prompt(goose_sid, message)
            except (ValueError, RuntimeError) as e:
                # Session was likely lost due to a restart
                print(f"[{datetime.now()}] Session {session_key} lost, retrying once: {e}")
                await api.create_post(channel_id, "🔄 *Notice: Connection to Goose was reset. I am starting a fresh session for this thread.*", root_id=root_id)
                sessions[session_key] = await goose.create_session()
                goose_sid = sessions[session_key]
                await run_prompt(goose_sid, message)

        except Exception as e:
            print(f"[{datetime.now()}] Error handling message for {session_key}: {e}")
            await api.create_post(channel_id, f"⚠️ Sorry, I encountered an error: {str(e)}", root_id=root_id)

async def run_bridge():
    api = MattermostAPI()
    goose = GooseACPClient()
    
    me = await api.get_me()
    if not me:
        print(f"[{datetime.now()}] Failed to connect to Mattermost. Check your URL and TOKEN.")
        return
    
    bot_id = me['id']
    bot_username = me['username']
    bot_mention = f"@{bot_username}"
    print(f"[{datetime.now()}] Connected as {bot_username} ({bot_id})")
    
    await goose.ensure_running()
    
    # Track sessions: key = user_id:root_id, value = goose_session_id
    sessions = {}
    session_locks = {}
    
    last_since = int(time.time() * 1000)
    print(f"[{datetime.now()}] Bridge is polling for messages. Press Ctrl+C to stop.")
    
    while True:
        try:
            # Get channels to check
            channels = await api.get_direct_channels() or []
            teams = await api.get_my_teams() or []
            for team in teams:
                team_channels = await api.get_my_channels(team['id']) or []
                channels.extend(team_channels)
            
            # De-duplicate channels and store types
            channel_map = {c['id']: c for c in channels}
            channel_ids = set(channel_map.keys())
            
            new_since = last_since
            for cid in channel_ids:
                posts_data = await api.get_channel_posts(cid, last_since)
                if not posts_data or "posts" not in posts_data:
                    continue
                
                # Sort posts by creation time
                sorted_posts = sorted(posts_data["posts"].values(), key=lambda x: x["create_at"])
                
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

                    # Check if we should respond
                    is_mentioned = bot_mention in message
                    
                    if not is_dm and not is_mentioned:
                        continue
                    
                    # Approved users check
                    if APPROVED_USERS:
                        user_info = api._request(f"/users/{sender_id}")
                        username = user_info.get("username") if user_info else None
                        if sender_id not in APPROVED_USERS and username not in APPROVED_USERS:
                            print(f"[{datetime.now()}] Ignoring message from unapproved user: {username or sender_id}")
                            continue
                    
                    # Spawn task to handle message
                    asyncio.create_task(handle_message(api, goose, post, sessions, session_locks, bot_mention))
                    # Prune old sessions if there are too many
    if len(sessions) > 100:
        # Simple FIFO pruning: remove the first 20 keys
        keys_to_remove = list(sessions.keys())[:20]
        for k in keys_to_remove:
            del sessions[k]
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