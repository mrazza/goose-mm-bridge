import asyncio
import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock


class MockProcess:
    """Mocks an asyncio subprocess for the Goose ACP client."""

    def __init__(self):
        self.stdin = MagicMock()
        self.stdin.write = MagicMock()
        self.stdin.drain = AsyncMock(return_value=None)
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.returncode = None
        self.terminate = MagicMock()

    def feed_stdout(self, data: dict):
        """Feeds a JSON-RPC message into the stdout stream."""
        line = json.dumps(data) + "\n"
        self.stdout.feed_data(line.encode())


async def simulate_goose_behavior(mock_process: MockProcess,
                                  session_id: str = "session_abc",
                                  final_text: str = "The answer is 42."):
    """Simulates a standard Goose ACP interaction sequence."""
    try:
        # 1. Handshake response (triggered by initialize)
        await asyncio.sleep(0.05)
        mock_process.feed_stdout({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "capabilities": {
                    "mcp": {
                        "sse": True
                    }
                }
            }
        })

        # 2. session/new response
        await asyncio.sleep(0.05)
        mock_process.feed_stdout({
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "sessionId": session_id
            }
        })

        # 3. Handle the prompt (which now includes both context and user message)
        await asyncio.sleep(0.05)

        # Thinking chunk
        mock_process.feed_stdout({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": session_id,
                "update": {
                    "sessionUpdate": "agent_thinking_chunk",
                    "thinking": "Analyzing..."
                }
            }
        })
        # Content chunk
        mock_process.feed_stdout({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": session_id,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "text",
                        "text": final_text
                    }
                }
            }
        })

        # Final response for the prompt (id: 3)
        mock_process.feed_stdout({
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "status": "completed"
            }
        })
    except Exception as e:
        print(f"Simulator Error: {e}")
