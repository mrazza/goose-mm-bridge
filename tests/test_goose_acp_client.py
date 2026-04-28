import pytest
import asyncio
import json
from unittest.mock import MagicMock, patch, AsyncMock
from goose_acp_client import GooseACPClient
from config import Config

@pytest.fixture
def config():
    return Config(rpc_timeout=1)

@pytest.fixture
async def client(config):
    # Patch subprocess globally for all tests to prevent accidental spawns
    with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
        # Mock initialize handshake to avoid it failing during client setup if it were called
        with patch('goose_acp_client.GooseACPClient._send_raw_request', new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = {"result": {}}
            client = GooseACPClient(config=config)
            yield client

@pytest.mark.asyncio
async def test_ensure_running(client):
    # Reset the mock to track fresh calls
    with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_exec.return_value = mock_process
        
        # Mock initialize handshake
        with patch.object(client, '_send_raw_request', new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = {"result": {}}
            await client.ensure_running()
            
            assert mock_exec.called
            assert client.process == mock_process

@pytest.mark.asyncio
async def test_send_request(client):
    mock_process = MagicMock()
    mock_process.returncode = None # Ensure it looks alive
    mock_process.stdin = MagicMock()
    mock_process.stdin.write = MagicMock()
    mock_process.stdin.drain = AsyncMock()
    client.process = mock_process
    
    # Simulate a response
    future = asyncio.Future()
    client.pending_requests[1] = future
    future.set_result({"id": 1, "result": "success"})
    
    # Mock _send_raw_request to avoid actual IO
    with patch.object(client, '_send_raw_request', new_callable=AsyncMock) as mock_raw:
        mock_raw.return_value = {"id": 1, "result": "success"}
        # ensure_running should see client.process is not None and returncode is None
        res = await client.send_request("test_method", {"param": 1})
        assert res["result"] == "success"

@pytest.mark.asyncio
async def test_parse_update_chunk(client):
    # Test content chunk
    chunk = {
        "method": "session/prompt/next",
        "params": {"chunk": {"type": "text", "text": "hello"}}
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed == {"type": "content", "text": "hello"}

    # Test thinking chunk
    chunk = {
        "method": "session/update",
        "params": {"update": {"sessionUpdate": "agent_thinking_chunk", "thinking": "reasoning"}}
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed == {"type": "thinking", "text": "reasoning"}

    # Test tool chunk
    chunk = {
        "method": "session/update",
        "params": {"update": {"sessionUpdate": "call_tool", "toolCall": {"name": "test_tool", "arguments": {}}}}
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed["type"] == "tool"
    assert parsed["name"] == "test_tool"

@pytest.mark.asyncio
async def test_process_death_during_prompt(client):
    mock_process = MagicMock()
    mock_process.returncode = None
    client.process = mock_process
    client.session_queues["session_1"] = asyncio.Queue()
    
    # Mock send_request to return a future that won't complete immediately
    prompt_done_future = asyncio.Future()
    
    async def mock_send_request(*args, **kwargs):
        return await prompt_done_future
        
    client.send_request = mock_send_request
    
    async def run_prompt():
        async for _ in client.prompt("session_1", "hello"):
            pass
            
    task = asyncio.create_task(run_prompt())
    
    # Give it a moment to enter the loop
    await asyncio.sleep(0.05)
    
    # Now kill the process
    mock_process.returncode = 1
    
    # The loop should check this and raise RuntimeError
    with pytest.raises(RuntimeError, match="Goose ACP process terminated during prompt"):
        await task

@pytest.mark.asyncio
async def test_rpc_timeout(client):
    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.stdin = MagicMock()
    mock_process.stdin.drain = AsyncMock()
    mock_process.terminate = MagicMock()
    client.process = mock_process
    
    # Mock _send_raw_request to never complete
    with patch.object(client, '_send_raw_request', new_callable=AsyncMock) as mock_raw:
        mock_raw.side_effect = asyncio.TimeoutError()
        
        with pytest.raises(asyncio.TimeoutError):
            await client.send_request("test", timeout=0.1)
        
        assert client._healthy is False
        assert mock_process.terminate.called