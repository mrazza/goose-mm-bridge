import asyncio
import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, Optional, Any, AsyncGenerator
from config import default_config

class GooseACPClient:
    """Client for interacting with the Goose ACP process."""

    def __init__(self, linux_user: Optional[str] = None, config=None):
        self.linux_user = linux_user
        self.config = config or default_config
        self.process = None
        self.message_id = 1
        self.pending_requests: Dict[int, asyncio.Future] = {}
        self.session_queues: Dict[str, asyncio.Queue] = {}
        self.active_prompts: Dict[str, int] = {}
        self.last_id_used = 0
        self._healthy = True
        self._start_lock = asyncio.Lock()

    async def ensure_running(self):
        """Ensures the Goose ACP process is running, restarting it if necessary."""
        async with self._start_lock:
            if self.process is None or self.process.returncode is not None or not self._healthy:
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
                self._healthy = True

    async def _start(self):
        """Starts the Goose ACP process."""
        print(f"[{datetime.now()}] Starting Goose ACP process...")
        cmd = ["goose", "acp"]
        if self.linux_user:
            import pwd
            try:
                home_dir = pwd.getpwnam(self.linux_user).pw_dir
                cmd = ["sudo", "-n", "-u", self.linux_user, "-D", home_dir] + cmd
            except KeyError:
                cmd = ["sudo", "-n", "-u", self.linux_user] + cmd

        print(f"[{datetime.now()}] Process command line: {cmd}")
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=10*1024*1024  # 10MB buffer for large JSON-RPC messages
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
            }), timeout=self.config.rpc_timeout)
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
        """Reads and processes stdout from the Goose ACP process."""
        while True:
            if self.process is None or self.process.stdout.at_eof():
                break
                
            line = await self.process.stdout.readline()
            if not line:
                break
            
            try:
                line_str = line.decode().strip()
                if not line_str: continue
                if self.config.debug:
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
        """Reads and processes stderr from the Goose ACP process."""
        while True:
            if self.process is None or self.process.stderr.at_eof():
                break
            line = await self.process.stderr.readline()
            if not line:
                break
            msg = line.decode().strip()
            if msg:
                print(f"[GOOSE-STDERR] {msg}", file=sys.stderr)

    async def _send_raw_request(self, method: str, params: dict = None, req_id: int = None) -> dict:
        """Sends a JSON-RPC request to the Goose ACP process without checking health."""
        if req_id is None:
            req_id = self.message_id
            self.message_id += 1
        self.last_id_used = req_id
        
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": req_id
        }
        
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending_requests[req_id] = future
        
        try:
            self.process.stdin.write((json.dumps(request) + "\n").encode())
            await self.process.stdin.drain()
            return await future
        finally:
            # Ensure we clean up the future if we were cancelled (e.g. timeout)
            self.pending_requests.pop(req_id, None)

    
    async def send_notification(self, method: str, params: dict = None):
        """Sends a JSON-RPC notification (no ID) to the Goose ACP process."""
        await self.ensure_running()
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
        }
        if self.config.debug:
            print(f"DEBUG: BRIDGE -> GOOSE (NOTIF): {method}({params})")
        self.process.stdin.write((json.dumps(notification) + "\n").encode())
        await self.process.stdin.drain()

    async def send_request(self, method: str, params: dict = None, timeout: Optional[int] = None, req_id: int = None) -> dict:
        """Sends a JSON-RPC request to the Goose ACP process."""
        await self.ensure_running()
        if self.config.debug:
            print(f"DEBUG: BRIDGE -> GOOSE: {method}({params})")
        
        wait_timeout = timeout if timeout is not None else self.config.rpc_timeout
        try:
            if wait_timeout <= 0:
                return await self._send_raw_request(method, params, req_id=req_id)
            return await asyncio.wait_for(self._send_raw_request(method, params, req_id=req_id), timeout=wait_timeout)
        except asyncio.TimeoutError:
            print(f"[{datetime.now()}] Request {method} timed out after {wait_timeout}s")
            self._healthy = False
            if self.process and self.process.returncode is None:
                print(f"[{datetime.now()}] Terminating unresponsive Goose ACP process...")
                try:
                    self.process.terminate()
                except Exception as e:
                    print(f"[{datetime.now()}] Error terminating process: {e}")
            raise

    async def create_session(self) -> str:
        """Creates a new session in the Goose ACP."""
        res = await self.send_request("session/new", {
            "cwd": os.getcwd(),
            "mcpServers": []
        })
        if "error" in res:
            raise Exception(f"Failed to create session: {res['error']}")
        session_id = res["result"]["sessionId"]
        self.session_queues[session_id] = asyncio.Queue()
        return session_id

    async def prompt(self, session_id: str, text: str) -> AsyncGenerator[Dict[str, Any], None]:
        """Sends a prompt to a session and yields updates."""
        if self.config.debug:
            print(f"DEBUG: Starting prompt for session {session_id}")
        
        if session_id not in self.session_queues:
            # Session might have been lost due to a restart
            raise ValueError(f"Session {session_id} not found (may have been reset)")

        # Clear existing chunks
        while not self.session_queues[session_id].empty():
            self.session_queues[session_id].get_nowait()
            
        prompt_id = self.message_id
        self.message_id += 1
        self.active_prompts[session_id] = prompt_id
        res_future = asyncio.create_task(self.send_request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}]
        }, timeout=0, req_id=prompt_id))
        
        full_response = ""
        last_activity = time.time()
        # Use a single try block for the entire loop to ensure cleanup in finally
        try:
            while True:
                # Wait for a chunk or the final response
                chunk_task = asyncio.create_task(self.session_queues[session_id].get())
                done, pending = await asyncio.wait(
                    [chunk_task, res_future], 
                    timeout=0.1,
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                if chunk_task in done:
                    last_activity = time.time()
                    chunk = chunk_task.result()
                    parsed = self._parse_update_chunk(chunk)
                    if parsed:
                        if parsed["type"] == "content":
                            full_response += parsed["text"]
                            yield {"type": "content", "text": full_response}
                        else:
                            yield parsed
                        
                elif not chunk_task.done():
                    chunk_task.cancel()

                if res_future in done:
                    if self.config.debug:
                        print(f"DEBUG: Final result for {session_id}")
                    # Final result received, but there might be more chunks in the queue
                    res = res_future.result()
                    if "error" in res:
                         raise Exception(f"Goose error: {res['error']}")
                         
                    full_response = await self._drain_remaining_chunks(session_id, full_response)
                    break
                    
                # If neither is done, check if process is still alive
                if self.process is None or self.process.returncode is not None:
                    raise RuntimeError("Goose ACP process terminated during prompt")
                
                # Check for inactivity timeout
                if time.time() - last_activity > self.config.rpc_timeout:
                    print(f"[{datetime.now()}] Request session/prompt timed out after {self.config.rpc_timeout}s")
                    self._healthy = False
                    if self.process and self.process.returncode is None:
                        print(f"[{datetime.now()}] Terminating unresponsive Goose ACP process...")
                        try:
                            self.process.terminate()
                        except Exception as e:
                            print(f"[{datetime.now()}] Error terminating process: {e}")
                    raise asyncio.TimeoutError(f"Request session/prompt timed out after {self.config.rpc_timeout}s")
                    
        except Exception as e:
            if not res_future.done():
                res_future.cancel()
            raise
        finally:
            self.active_prompts.pop(session_id, None)
        
        yield {"type": "final", "text": full_response}

    def _parse_update_chunk(self, chunk: dict) -> Optional[dict]:
        """Parses a chunk from the Goose ACP and returns a unified update dictionary."""
        if self.config.debug:
            print(f"DEBUG: Parsing chunk: {chunk}")
        params = chunk.get("params", {})
        update = params.get("update", {})
        
        # Handle session/prompt/next format
        if params.get("chunk", {}).get("type") == "text":
            return {"type": "content", "text": params["chunk"]["text"]}
        
        # Handle session/update format
        session_update = update.get("sessionUpdate")
        if session_update == "agent_message_chunk":
            content_obj = update.get("content", {})
            if content_obj.get("type") == "text":
                return {"type": "content", "text": content_obj.get("text", "")}
        elif session_update == "agent_thinking_chunk":
            return {"type": "thinking", "text": update.get("thinking", "")}
        elif session_update == "call_tool":
            tool_call = update.get("toolCall", {})
            return {"type": "tool", "name": tool_call.get("name"), "arguments": tool_call.get("arguments")}
        elif session_update == "tool_call":
            return {"type": "tool", "name": update.get("title") or "tool", "arguments": {}}
        elif session_update == "tool_call_update":
            title = update.get("title")
            if title:
                return {"type": "thinking", "text": f"\n**Updated**: `{title}`\n"}
        
        if self.config.debug:
            print(f"DEBUG: Unknown or unhandled chunk format: {chunk}")
        return None

    async def _drain_remaining_chunks(self, session_id: str, full_response: str) -> str:
        """Drains any remaining chunks from the session queue after the final response."""
        while not self.session_queues[session_id].empty():
            chunk = await self.session_queues[session_id].get()
            if self.config.debug:
                print(f"DEBUG: Draining late chunk for {session_id}")
            
            parsed = self._parse_update_chunk(chunk)
            if parsed and parsed["type"] == "content":
                full_response += parsed["text"]
        return full_response

    async def cancel_prompt(self, session_id: str):
        """Cancels the currently active prompt for a session."""
        if session_id in self.active_prompts:
            msg_id = self.active_prompts[session_id]
            await self.send_notification("session/cancel", {
                "sessionId": session_id,
                "messageId": msg_id
            })
            return True
        return False
