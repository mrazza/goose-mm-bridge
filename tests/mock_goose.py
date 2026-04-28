import asyncio
import json
import unittest.mock

class MockStreamReader:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.eof = False

    async def readline(self):
        if self.eof and self.queue.empty():
            return b""
        try:
            return await self.queue.get()
        except asyncio.CancelledError:
            return b""

    def at_eof(self):
        return self.eof and self.queue.empty()

    def feed(self, data: bytes):
        self.queue.put_nowait(data)

class MockSubprocess:
    def __init__(self, stdin_mock=None):
        if stdin_mock is None:
            stdin_mock = unittest.mock.MagicMock()
            stdin_mock.drain = unittest.mock.AsyncMock()
            stdin_mock.write = unittest.mock.MagicMock()
        self.stdin = stdin_mock
        self.stdout = MockStreamReader()
        self.stderr = MockStreamReader()
        self.returncode = None
        self.pid = 1234

    def terminate(self):
        self.returncode = 0
        self.stdout.eof = True
        self.stderr.eof = True

async def process_manager(mock_proc):
    """Simulates the Goose ACP process behavior."""
    try:
        while mock_proc.returncode is None:
            if mock_proc.stdin.write.called:
                # Get all calls and process them
                calls = list(mock_proc.stdin.write.call_args_list)
                mock_proc.stdin.write.reset_mock()
                
                for args in calls:
                    if not args[0]: continue
                    data = args[0][0].decode()
                    for line in data.strip().split('\n'):
                        if not line: continue
                        try:
                            request = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                            
                        method = request.get("method")
                        req_id = request.get("id")
                        
                        if method == "initialize":
                            response = {"jsonrpc": "2.0", "id": req_id, "result": {"capabilities": {}}}
                            mock_proc.stdout.feed((json.dumps(response) + "\n").encode())
                        elif method == "session/new":
                            response = {"jsonrpc": "2.0", "id": req_id, "result": {"sessionId": "session_123"}}
                            mock_proc.stdout.feed((json.dumps(response) + "\n").encode())
                        elif method == "session/prompt":
                            # 1. Send thinking update
                            update1 = {
                                "jsonrpc": "2.0", 
                                "method": "session/update", 
                                "params": {
                                    "sessionId": "session_123",
                                    "update": {"sessionUpdate": "agent_thinking_chunk", "thinking": "I am thinking..."}
                                }
                            }
                            mock_proc.stdout.feed((json.dumps(update1) + "\n").encode())
                            
                            # 2. Send content update
                            update2 = {
                                "jsonrpc": "2.0", 
                                "method": "session/update", 
                                "params": {
                                    "sessionId": "session_123",
                                    "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "The answer is 42."}}
                                }
                            }
                            mock_proc.stdout.feed((json.dumps(update2) + "\n").encode())
                            
                            # 3. Send final result
                            response = {"jsonrpc": "2.0", "id": req_id, "result": {"status": "success"}}
                            mock_proc.stdout.feed((json.dumps(response) + "\n").encode())

            await asyncio.sleep(0.01)
    except asyncio.CancelledError:
        pass
