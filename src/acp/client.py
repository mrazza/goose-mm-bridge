import asyncio
import json
import os
import sys
import time
from typing import Any, AsyncGenerator, Dict, Optional

from acp.events import parse_update_chunk
from acp.launcher import build_agent_launch_spec
from acp.launcher import terminate_process
from config import default_config
from config import load_user_config
from logging_config import get_logger

logger = get_logger(__name__)


class ACPClient:
    """Client for interacting with an ACP-compatible agent process."""

    def __init__(
        self,
        linux_user: Optional[str] = None,
        agent_name: Optional[str] = None,
        config=None,
    ):
        self.linux_user = linux_user
        if linux_user and config:
            self.config = load_user_config(linux_user, config)
        else:
            self.config = config or default_config

        self.agent_name = agent_name or self.config.default_agent
        self.process = None
        self.message_id = 1
        self.pending_requests: Dict[int, asyncio.Future] = {}
        self.session_queues: Dict[str, asyncio.Queue] = {}
        self.active_prompts: Dict[str, int] = {}
        self.sse_supported = False
        self.http_supported = False
        self.last_id_used = 0
        self._healthy = True
        self._start_lock = asyncio.Lock()
        self.context_usage: Dict[str, Dict[str, int]] = {}
        self.session_models: Dict[str, Dict[str, Any]] = {}
        self.last_known_models: Optional[Dict[str, Any]] = None

    async def ensure_running(self):
        """Ensures the agent ACP process is running, restarting if needed."""
        async with self._start_lock:
            if (self.process is None or self.process.returncode is not None or
                    not self._healthy):
                if self.process is not None:
                    logger.warning(
                        "Agent '%s' ACP process died (code %s). Restarting...",
                        self.agent_name,
                        self.process.returncode,
                    )
                    for fut in self.pending_requests.values():
                        if not fut.done():
                            fut.set_exception(
                                RuntimeError(f"Agent '{self.agent_name}' ACP "
                                             f"process terminated"))
                    self.pending_requests.clear()
                    self.session_queues.clear()
                    self.session_models.clear()
                    self.context_usage.clear()

                await self._start()
                self._healthy = True

    async def _start(self):
        """Starts the agent ACP process."""
        logger.info("Starting agent '%s' ACP process...", self.agent_name)
        cmd, env = build_agent_launch_spec(self.agent_name,
                                           self.config,
                                           linux_user=self.linux_user)

        logger.info("Process command line: %s", cmd)
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=10 * 1024 * 1024,
        )
        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())

        try:
            res = await asyncio.wait_for(
                self._send_raw_request(
                    "initialize",
                    {
                        "protocolVersion": 0,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "mattermost-agent-bridge",
                            "version": "1.0.0",
                        },
                    },
                ),
                timeout=self.config.rpc_timeout,
            )
            result = res.get("result") or {}
            capabilities = result.get("agentCapabilities") or {}
            mcp_caps = (capabilities.get("mcpCapabilities") if isinstance(
                capabilities, dict) else None)
            if not isinstance(mcp_caps, dict):
                mcp_caps = {}

            self.sse_supported = bool(mcp_caps.get("sse", False))
            self.http_supported = bool(mcp_caps.get("http", False))
            logger.info(
                "Agent '%s' ACP initialized. SSE support: %s, HTTP support: %s",
                self.agent_name,
                self.sse_supported,
                self.http_supported,
            )
        except Exception as e:
            logger.error("Failed to initialize Agent '%s' ACP: %s",
                         self.agent_name, e)
            if self.process:
                try:
                    self.process.terminate()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                self.process = None
            raise

    async def _read_stdout(self):
        """Reads and processes stdout from the agent ACP process."""
        while True:
            if self.process is None or self.process.stdout.at_eof():
                break

            line = await self.process.stdout.readline()
            if not line:
                break

            try:
                line_str = line.decode().strip()
                if not line_str:
                    continue
                if self.config.debug:
                    logger.debug("AGENT -> BRIDGE: %s", line_str)
                res = json.loads(line_str)
                req_id = res.get("id")

                if req_id is not None and req_id in self.pending_requests:
                    if not self.pending_requests[req_id].done():
                        self.pending_requests[req_id].set_result(res)
                    del self.pending_requests[req_id]

                if res.get("method") in [
                        "session/prompt/next", "session/update"
                ]:
                    params = res.get("params", {})
                    session_id = params.get("sessionId")
                    if session_id and session_id in self.session_queues:
                        await self.session_queues[session_id].put(res)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Error parsing agent output: %s", e)

        for fut in list(self.pending_requests.values()):
            if not fut.done():
                fut.set_exception(RuntimeError("Agent ACP stdout closed"))
        self.pending_requests.clear()
        logger.info("Agent '%s' ACP stdout closed.", self.agent_name)

    async def _read_stderr(self):
        """Reads and processes stderr from the agent ACP process."""
        while True:
            if self.process is None or self.process.stderr.at_eof():
                break
            line = await self.process.stderr.readline()
            if not line:
                break
            msg = line.decode().strip()
            if msg:
                print(f"[AGENT-STDERR] {msg}", file=sys.stderr)

    async def _send_raw_request(
        self,
        method: str,
        params: Optional[dict] = None,
        req_id: Optional[int] = None,
    ) -> dict:
        """Sends a JSON-RPC request to the agent without checking health."""
        if req_id is None:
            req_id = self.message_id
            self.message_id += 1
        self.last_id_used = req_id

        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": req_id,
        }

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending_requests[req_id] = future

        try:
            self.process.stdin.write((json.dumps(request) + "\n").encode())
            await self.process.stdin.drain()
            return await future
        finally:
            self.pending_requests.pop(req_id, None)

    async def send_notification(self,
                                method: str,
                                params: Optional[dict] = None):
        """Sends a JSON-RPC notification (no ID) to the agent ACP process."""
        await self.ensure_running()
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        if self.config.debug:
            logger.debug("BRIDGE -> AGENT (NOTIF): %s(%s)", method, params)
        self.process.stdin.write((json.dumps(notification) + "\n").encode())
        await self.process.stdin.drain()

    async def send_request(
        self,
        method: str,
        params: Optional[dict] = None,
        timeout: Optional[float] = None,
        req_id: Optional[int] = None,
    ) -> dict:
        """Sends a JSON-RPC request to the agent ACP process."""
        await self.ensure_running()
        if self.config.debug:
            logger.debug("BRIDGE -> AGENT: %s(%s)", method, params)

        wait_timeout = (timeout
                        if timeout is not None else self.config.rpc_timeout)
        try:
            if wait_timeout <= 0:
                return await self._send_raw_request(method,
                                                    params,
                                                    req_id=req_id)
            return await asyncio.wait_for(
                self._send_raw_request(method, params, req_id=req_id),
                timeout=wait_timeout,
            )
        except asyncio.TimeoutError:
            logger.error("Request %s timed out after %ss", method, wait_timeout)
            self._healthy = False
            if self.process and self.process.returncode is None:
                logger.warning("Terminating unresponsive Agent ACP process...")
                try:
                    self.process.terminate()
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.error("Error terminating process: %s", e)
            raise

    async def create_session(self) -> str:
        """Creates a new session in the agent ACP."""
        await self.ensure_running()

        params = {"cwd": os.getcwd(), "mcpServers": self._get_mcp_servers()}
        res = await self.send_request("session/new", params)
        if "error" in res:
            raise RuntimeError(f"Failed to create session: {res['error']}")

        result = res.get("result", {})
        session_id = result["sessionId"]
        self.session_queues[session_id] = asyncio.Queue()

        models_data = result.get("models")
        if isinstance(models_data, dict):
            current_model = models_data.get(
                "currentModelId") or models_data.get("currentModel")
            available_models = models_data.get("availableModels", [])
            model_info = {
                "current_model": current_model,
                "available_models": list(available_models),
            }
            self.session_models[session_id] = dict(model_info)
            self.last_known_models = dict(model_info)

        return session_id

    async def set_model(self, session_id: str, model_id: str) -> dict:
        """Sets the active model for a session in the agent ACP."""
        await self.ensure_running()
        res = await self.send_request(
            "session/set_model",
            {
                "sessionId": session_id,
                "modelId": model_id,
            },
        )
        if "error" in res:
            raise RuntimeError(f"Failed to set model: {res['error']}")
        if session_id in self.session_models:
            self.session_models[session_id]["current_model"] = model_id
        else:
            avail = (self.last_known_models.get("available_models", [])
                     if self.last_known_models else [])
            self.session_models[session_id] = {
                "current_model": model_id,
                "available_models": list(avail),
            }
        return res.get("result", {})

    def get_models(
            self, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Returns model metadata for a given session, or last known models."""
        if session_id and session_id in self.session_models:
            return self.session_models[session_id]
        return self.last_known_models

    async def _send_close_rpc(self, session_id: str, timeout: float) -> None:
        try:
            await asyncio.wait_for(
                self.send_request("session/close", {"sessionId": session_id}),
                timeout=timeout,
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            if self.config.debug:
                logger.debug("Error closing session %s: %s", session_id, e)

    async def close_session(
        self,
        session_id: str,
        await_rpc: bool = False,
        cancel_first: bool = False,
        timeout: float = 3.0,
    ) -> None:
        """Cleans up local session state and closes the ACP session."""
        if cancel_first:
            try:
                await self.cancel_prompt(session_id)
            except Exception as e:  # pylint: disable=broad-exception-caught
                if self.config.debug:
                    logger.debug("Error cancelling prompt for %s: %s",
                                 session_id, e)

        self.session_queues.pop(session_id, None)
        self.session_models.pop(session_id, None)
        self.context_usage.pop(session_id, None)
        self.active_prompts.pop(session_id, None)

        if await_rpc:
            await self._send_close_rpc(session_id, timeout)
        else:
            asyncio.create_task(self._send_close_rpc(session_id, timeout))

    async def terminate(self, timeout: float = 3.0) -> None:
        """Terminates the ACP subprocess and clears all session state."""
        self.session_queues.clear()
        self.session_models.clear()
        self.context_usage.clear()
        self.active_prompts.clear()

        await terminate_process(self.process,
                                timeout=timeout,
                                debug=self.config.debug)

    async def prompt(self, session_id: str,
                     text: str) -> AsyncGenerator[Dict[str, Any], None]:
        """Sends a prompt to a session and yields updates."""
        if self.config.debug:
            logger.debug("Starting prompt for session %s", session_id)

        if session_id not in self.session_queues:
            raise ValueError(
                f"Session {session_id} not found (may have been reset)")

        while not self.session_queues[session_id].empty():
            self.session_queues[session_id].get_nowait()

        prompt_id = self.message_id
        self.message_id += 1
        self.active_prompts[session_id] = prompt_id
        res_future = asyncio.create_task(
            self.send_request(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": [{
                        "type": "text",
                        "text": text
                    }],
                },
                timeout=0,
                req_id=prompt_id,
            ))

        full_response = ""
        last_activity = time.time()
        try:
            while True:
                chunk_task = asyncio.create_task(
                    self.session_queues[session_id].get())
                done, _ = await asyncio.wait(
                    [chunk_task, res_future],
                    timeout=0.1,
                    return_when=asyncio.FIRST_COMPLETED,
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
                        logger.debug("Final result for %s", session_id)
                    res = res_future.result()
                    if "error" in res:
                        raise RuntimeError(f"Agent error: {res['error']}")

                    result = res.get("result", {})
                    stop_reason = (result.get("stopReason") if isinstance(
                        result, dict) else None)

                    full_response = await self._drain_remaining_chunks(
                        session_id, full_response)

                    if stop_reason == "max_turns":
                        full_response += ("\n\n⚠️ *Warning: Session reached "
                                          "maximum turn limit.*")
                    elif stop_reason == "max_tokens":
                        full_response += ("\n\n⚠️ *Warning: Session reached "
                                          "context token limit.*")
                    elif stop_reason == "cancelled":
                        full_response += (
                            "\n\n🛑 *Notice: Session prompt was cancelled.*")

                    break

                if self.process is None or self.process.returncode is not None:
                    raise RuntimeError(
                        "Agent ACP process terminated during prompt")

                if time.time() - last_activity > self.config.rpc_timeout:
                    logger.error("Request session/prompt timed out after %ss",
                                 self.config.rpc_timeout)
                    self._healthy = False
                    if self.process and self.process.returncode is None:
                        logger.warning(
                            "Terminating unresponsive Agent ACP process...")
                        try:
                            self.process.terminate()
                        except Exception as e:  # pylint: disable=broad-exception-caught
                            logger.error("Error terminating process: %s", e)
                    raise asyncio.TimeoutError(
                        f"Request session/prompt timed out after "
                        f"{self.config.rpc_timeout}s")

        except Exception:
            if not res_future.done():
                res_future.cancel()
            raise
        finally:
            self.active_prompts.pop(session_id, None)

        yield {"type": "final", "text": full_response}

    def _parse_update_chunk(self, chunk: dict) -> Optional[dict]:
        """Parses a chunk from the Agent ACP and updates usage state."""
        parsed = parse_update_chunk(chunk, debug=self.config.debug)
        if parsed and parsed["type"] == "usage":
            session_id = chunk.get("params", {}).get("sessionId")
            if session_id:
                self.context_usage[session_id] = {
                    "used": parsed["used"],
                    "size": parsed["size"],
                }
        return parsed

    async def _drain_remaining_chunks(self, session_id: str,
                                      full_response: str) -> str:
        """Drains remaining chunks from the session queue after completion."""
        if session_id not in self.session_queues:
            return full_response
        while not self.session_queues[session_id].empty():
            chunk = await self.session_queues[session_id].get()
            if self.config.debug:
                logger.debug("Draining late chunk for %s", session_id)
            parsed = self._parse_update_chunk(chunk)
            if parsed and parsed["type"] == "content":
                full_response += parsed["text"]
        return full_response

    async def cancel_prompt(self, session_id: str):
        """Cancels the currently active prompt for a session."""
        if session_id in self.active_prompts:
            msg_id = self.active_prompts[session_id]
            await self.send_notification(
                "session/cancel",
                {
                    "sessionId": session_id,
                    "messageId": msg_id,
                },
            )
            return True
        return False

    def _get_mcp_servers(self):
        servers = []
        if self.config.mcp_enabled and self.http_supported:
            servers.append({
                "type":
                    "http",
                "name":
                    "mattermost-bridge",
                "url":
                    f"http://{self.config.mcp_host}:{self.config.mcp_port}/mcp",
                "headers": [],
            })

        if self.config.goose_mcp_servers:
            servers.extend(self.config.goose_mcp_servers)

        return servers
