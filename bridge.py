import asyncio
import json
import os
import sys
import time
import ssl
import urllib.request
import urllib.error
from datetime import datetime
from typing import Dict, List, Optional
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

class GooseACPClient:
    def __init__(self):
        self.process = None
        self.message_id = 1
        self.pending_requests: Dict[int, asyncio.Future] = {}
        self.session_queues: Dict[str, asyncio.Queue] = {}

    async def start(self):
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
        await self.send_request("initialize", {
            "protocolVersion": 0,
            "capabilities": {},
            "clientInfo": {"name": "goose-mm-bridge", "version": "1.0.0"}
        })
        print(f"[{datetime.now()}] Goose ACP initialized.")

    async def _read_stdout(self):
        while True:
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
                    if DEBUG:
                        print(f"DEBUG: Received chunk for session update")
                    session_id = res.get("params", {}).get("sessionId")
                    # If sessionId is not in params, use the last active session as a fallback
                    # or better, send to all queues if we can't distinguish.
                    # For now, let's check if sessionId is there.
                    if session_id:
                        if session_id in self.session_queues:
                            await self.session_queues[session_id].put(res)
                    else:
                        # Fallback: put in all queues or log error
                        for q in self.session_queues.values():
                            await q.put(res)
            except Exception as e:
                print(f"Error parsing Goose output: {e}")
                if 'line' in locals():
                    print(f"DEBUG: Failed line content: {line}")

    async def _read_stderr(self):
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            # Skip noise but log important things
            msg = line.decode().strip()
            if msg:
                print(f"[GOOSE-STDERR] {msg}", file=sys.stderr)

    async def send_request(self, method: str, params: dict = None) -> dict:
        if DEBUG:
            print(f"DEBUG: BRIDGE -> GOOSE: {method}({params})")
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
                # Wait for a chunk with a timeout, or the final response
                chunk_task = asyncio.create_task(self.session_queues[session_id].get())
                done, pending = await asyncio.wait(
                    [chunk_task, res_future], 
                    timeout=0.1,
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                if chunk_task in done:
                    chunk = chunk_task.result()
                    if DEBUG:
                        print(f"DEBUG: Processing chunk for {session_id}")
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
                            yield {"type": "thinking", "text": f"\n*Updated: {title}*\n"}
                        
                elif not chunk_task.done():
                    chunk_task.cancel()

                if res_future in done:
                    if DEBUG:
                        print(f"DEBUG: session/prompt request finished for {session_id}")
                    # Final result received, but there might be more chunks in the queue
                    # Drain the queue
                    while not self.session_queues[session_id].empty():
                        chunk = await self.session_queues[session_id].get()
                        if DEBUG:
                            print(f"DEBUG: Draining late chunk for {session_id}")
                        if chunk.get("params", {}).get("chunk", {}).get("type") == "text":
                            full_response += chunk["params"]["chunk"]["text"]
                    break
            except Exception as e:
                print(f"Error in prompt loop: {e}")
                break
        
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

    def _request(self, path, data=None, method="GET"):
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
        return self._request("/users/me")

    def get_direct_channels(self):
        return self._request("/users/me/channels")

    def get_my_teams(self):
        return self._request("/users/me/teams")

    def get_my_channels(self, team_id):
        return self._request(f"/users/me/teams/{team_id}/channels")

    def get_channel_posts(self, channel_id, since):
        return self._request(f"/channels/{channel_id}/posts?since={since}")

    def create_post(self, channel_id, message, root_id=None):
        data = {"channel_id": channel_id, "message": message, "root_id": root_id}
        return self._request("/posts", data=data, method="POST")

    def update_post(self, post_id, message):
        data = {"id": post_id, "message": message}
        return self._request(f"/posts/{post_id}", data=data, method="PUT")

async def run_bridge():
    api = MattermostAPI()
    goose = GooseACPClient()
    
    me = api.get_me()
    if not me:
        print(f"[{datetime.now()}] Failed to connect to Mattermost. Check your URL and TOKEN.")
        return
    
    bot_id = me['id']
    print(f"[{datetime.now()}] Connected as {me['username']} ({bot_id})")
    
    await goose.start()
    
    # Track sessions: key = user_id:root_id, value = goose_session_id
    sessions = {}
    
    # We poll every 3 seconds
    last_since = int(time.time() * 1000)
    print(f"[{datetime.now()}] Bridge is polling for messages. Press Ctrl+C to stop.")
    
    while True:
        try:
            # Get channels to check
            channels = api.get_direct_channels() or []
            teams = api.get_my_teams() or []
            for team in teams:
                team_channels = api.get_my_channels(team['id']) or []
                channels.extend(team_channels)
            
            # De-duplicate channels
            channel_ids = {c['id'] for c in channels}
            
            new_since = last_since
            for cid in channel_ids:
                posts_data = api.get_channel_posts(cid, last_since)
                if not posts_data or "posts" not in posts_data:
                    continue
                
                # Sort posts by creation time
                sorted_posts = sorted(posts_data["posts"].values(), key=lambda x: x["create_at"])
                
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
                    
                    # Approved users check
                    if APPROVED_USERS:
                        user_info = api._request(f"/users/{sender_id}")
                        username = user_info.get("username") if user_info else None
                        if sender_id not in APPROVED_USERS and username not in APPROVED_USERS:
                            print(f"[{datetime.now()}] Ignoring message from unapproved user: {username or sender_id}")
                            continue
                    
                    root_id = post.get("root_id") or post["id"]
                    session_key = f"{sender_id}:{root_id}"
                    
                    if session_key not in sessions:
                        print(f"[{datetime.now()}] Creating new Goose session for {session_key}")
                        sessions[session_key] = await goose.create_session()
                    
                    goose_sid = sessions[session_key]
                    print(f"[{datetime.now()}] User {sender_id} says: {message[:100]}...")
                    
                    thinking_post = None
                    full_response = ""
                    thinking_trace = ""
                    last_update_time = 0
                    
                    async for update in goose.prompt(goose_sid, message):
                        if update["type"] == "thinking":
                            thinking_trace += update["text"]
                        elif update["type"] == "tool":
                            thinking_trace += f"\n\n*Using tool: **{update['name']}***\n"
                        elif update["type"] == "content":
                            full_response = update["text"]
                        elif update["type"] == "final":
                            full_response = update["text"]

                        # Update thinking post periodically or if it's the final response
                        current_time = time.time()
                        if (thinking_trace and current_time - last_update_time > 1.0) or update["type"] == "final":
                            msg = ""
                            if update["type"] != "final":
                                msg = f":thinking_face: **Thinking...**\n\n{thinking_trace}"
                                if full_response:
                                    msg += f"\n\n---\n\n{full_response}"
                            else:
                                msg = full_response
                            
                            if not thinking_post:
                                thinking_post = api.create_post(post["channel_id"], msg, root_id=root_id)
                            else:
                                api.update_post(thinking_post["id"], msg)
                            last_update_time = current_time
            
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