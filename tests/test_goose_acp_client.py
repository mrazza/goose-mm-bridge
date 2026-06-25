import asyncio
import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from config import Config
from goose_acp_client import GooseACPClient


@pytest.fixture
def config():
    return Config(rpc_timeout=1)


@pytest.fixture
async def client(config):
    # Patch subprocess globally for all tests to prevent accidental spawns
    with patch('asyncio.create_subprocess_exec',
               new_callable=AsyncMock) as mock_exec:
        # Mock initialize handshake to avoid it failing during client setup if it were called
        with patch('goose_acp_client.GooseACPClient._send_raw_request',
                   new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = {"result": {}}
            client = GooseACPClient(config=config)
            yield client


@pytest.mark.asyncio
async def test_ensure_running(client):
    # Reset the mock to track fresh calls
    with patch('asyncio.create_subprocess_exec',
               new_callable=AsyncMock) as mock_exec:
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_exec.return_value = mock_process

        # Mock initialize handshake
        with patch.object(client, '_send_raw_request',
                          new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = {"result": {}}
            await client.ensure_running()

            assert mock_exec.called
            assert client.process == mock_process


@pytest.mark.asyncio
async def test_send_request(client):
    mock_process = MagicMock()
    mock_process.returncode = None  # Ensure it looks alive
    mock_process.stdin = MagicMock()
    mock_process.stdin.write = MagicMock()
    mock_process.stdin.drain = AsyncMock()
    client.process = mock_process

    # Simulate a response
    future = asyncio.Future()
    client.pending_requests[1] = future
    future.set_result({"id": 1, "result": "success"})

    # Mock _send_raw_request to avoid actual IO
    with patch.object(client, '_send_raw_request',
                      new_callable=AsyncMock) as mock_raw:
        mock_raw.return_value = {"id": 1, "result": "success"}
        # ensure_running should see client.process is not None and returncode is None
        res = await client.send_request("test_method", {"param": 1})
        assert res["result"] == "success"


@pytest.mark.asyncio
async def test_parse_update_chunk(client):
    # Test content chunk
    chunk = {
        "method": "session/prompt/next",
        "params": {
            "chunk": {
                "type": "text",
                "text": "hello"
            }
        }
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed == {"type": "content", "text": "hello"}

    # Test thinking chunk
    chunk = {
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "agent_thinking_chunk",
                "thinking": "reasoning"
            }
        }
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed == {"type": "thinking", "text": "reasoning"}

    # Test tool chunk
    chunk = {
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "call_tool",
                "toolCall": {
                    "name": "test_tool",
                    "arguments": {}
                }
            }
        }
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed["type"] == "tool"
    assert parsed["name"] == "test_tool"

    # Test tool_call_update with title
    chunk = {
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "tool_call_update",
                "title": "Performing search..."
            }
        }
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed == {"type": "thinking", "text": "Performing search..."}

    # Test tool_call_update with completed status
    chunk = {
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "tool_call_update",
                "status": "completed"
            }
        }
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed == {"type": "thinking", "text": "working..."}


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
    with pytest.raises(RuntimeError,
                       match="Goose ACP process terminated during prompt"):
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
    with patch.object(client, '_send_raw_request',
                      new_callable=AsyncMock) as mock_raw:
        mock_raw.side_effect = asyncio.TimeoutError()

        with pytest.raises(asyncio.TimeoutError):
            await client.send_request("test", timeout=0.1)

        assert client._healthy is False
        assert mock_process.terminate.called


@pytest.mark.asyncio
async def test_large_message_handling(client):
    mock_process = MagicMock()
    mock_process.returncode = None
    # Create a large response (~1MB)
    large_text = "a" * (1024 * 1024)
    large_json = json.dumps({"id": 1, "result": {"data": large_text}}) + "\n"

    # Mock stdout to return this large line
    mock_process.stdout.readline = AsyncMock(
        side_effect=[large_json.encode(), b""])
    mock_process.stdout.at_eof = MagicMock(side_effect=[False, True])
    client.process = mock_process

    future = asyncio.Future()
    client.pending_requests[1] = future

    # Start the reader task
    reader = asyncio.create_task(client._read_stdout())

    # Wait for the future to be resolved by the reader
    res = await asyncio.wait_for(future, timeout=2)
    assert res["result"]["data"] == large_text
    await reader


@pytest.mark.asyncio
async def test_drain_remaining_chunks_unit(client):
    session_id = "session_123"
    client.session_queues[session_id] = asyncio.Queue()

    # Put some late chunks in
    await client.session_queues[session_id].put({
        "method": "session/prompt/next",
        "params": {
            "chunk": {
                "type": "text",
                "text": "late "
            }
        }
    })
    await client.session_queues[session_id].put({
        "method": "session/prompt/next",
        "params": {
            "chunk": {
                "type": "text",
                "text": "data"
            }
        }
    })

    final_res = await client._drain_remaining_chunks(session_id, "initial ")
    assert final_res == "initial late data"
    assert client.session_queues[session_id].empty()


@pytest.mark.asyncio
async def test_get_mcp_servers(client):
    client.config.mcp_enabled = True
    client.config.mcp_host = "localhost"
    client.config.mcp_port = 5006
    client.config.goose_mcp_servers = []

    # Test with HTTP support
    client.http_supported = True
    servers = client._get_mcp_servers()
    assert len(servers) == 1
    assert servers[0]["type"] == "http"
    assert "5006/mcp" in servers[0]["url"]

    # Test without HTTP support
    client.http_supported = False
    servers = client._get_mcp_servers()
    assert len(servers) == 0

    # Test with HTTP support but disabled
    client.http_supported = True
    client.config.mcp_enabled = False
    servers = client._get_mcp_servers()
    assert len(servers) == 0

@pytest.mark.asyncio
async def test_ensure_running_died_restarts(client):
    # Tests lines 35-45 (died process)
    mock_process = MagicMock()
    mock_process.returncode = 127
    client.process = mock_process
    
    future = asyncio.Future()
    client.pending_requests[1] = future
    client.session_queues["s1"] = asyncio.Queue()

    with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
        mock_new_process = MagicMock()
        mock_new_process.returncode = None
        mock_exec.return_value = mock_new_process

        with patch.object(client, '_send_raw_request', new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = {"result": {}}
            await client.ensure_running()

            assert mock_exec.called
            assert client.process == mock_new_process
            assert future.done()
            with pytest.raises(RuntimeError, match="Goose ACP process terminated"):
                await future
            assert len(client.pending_requests) == 0
            assert len(client.session_queues) == 0

@pytest.mark.asyncio
async def test_start_with_linux_user_and_pwd(config):
    # Tests lines 58 (pwd.getpwnam)
    config.debug = True
    client = GooseACPClient(config=config, linux_user="testuser")
    
    mock_pw = MagicMock()
    mock_pw.pw_dir = "/home/testuser"
    
    with patch('pwd.getpwnam', return_value=mock_pw):
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
            mock_process = MagicMock()
            mock_process.returncode = None
            mock_exec.return_value = mock_process
            
            with patch.object(client, '_send_raw_request', new_callable=AsyncMock) as mock_raw:
                mock_raw.return_value = {"result": {}}
                await client._start()
                
                args, kwargs = mock_exec.call_args
                assert "sudo" in args
                assert "testuser" in args
                assert "/home/testuser" in args

@pytest.mark.asyncio
async def test_start_handshake_failure(config):
    # Tests lines 95-103
    client = GooseACPClient(config=config)
    
    with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_exec.return_value = mock_process
        
        with patch.object(client, '_send_raw_request', new_callable=AsyncMock) as mock_raw:
            mock_raw.side_effect = Exception("Handshake timeout")
            
            with pytest.raises(Exception, match="Handshake timeout"):
                await client._start()
            
            assert mock_process.terminate.called
            assert client.process is None

@pytest.mark.asyncio
async def test_start_handshake_failure_terminate_exception(config):
    # Tests lines 100-101 (terminate raises exception)
    client = GooseACPClient(config=config)
    
    with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock(side_effect=Exception("Failed to terminate"))
        mock_exec.return_value = mock_process
        
        with patch.object(client, '_send_raw_request', new_callable=AsyncMock) as mock_raw:
            mock_raw.side_effect = Exception("Handshake timeout")
            
            with pytest.raises(Exception, match="Handshake timeout"):
                await client._start()
            
            assert mock_process.terminate.called
            assert client.process is None

@pytest.mark.asyncio
async def test_read_stdout_empty_and_invalid_json(client):
    # Tests lines 113, 118, 136-137
    mock_process = MagicMock()
    mock_process.stdout.readline = AsyncMock(side_effect=[
        b"\n",  # empty line_str -> continue (line 118)
        b"invalid json\n",  # parsing error -> print error (line 136-137)
        b""  # EOF -> break (line 113)
    ])
    mock_process.stdout.at_eof = MagicMock(side_effect=[False, False, False, True])
    client.process = mock_process
    
    await client._read_stdout()

@pytest.mark.asyncio
async def test_read_stdout_closes_pending_requests(client):
    # Tests lines 141-142
    mock_process = MagicMock()
    mock_process.stdout.readline = AsyncMock(return_value=b"")
    mock_process.stdout.at_eof = MagicMock(return_value=True)
    client.process = mock_process
    
    fut = asyncio.Future()
    client.pending_requests[1] = fut
    
    await client._read_stdout()
    assert fut.done()
    with pytest.raises(RuntimeError, match="Goose ACP stdout closed"):
        await fut

@pytest.mark.asyncio
async def test_read_stderr(client):
    # Tests lines 152-156
    mock_process = MagicMock()
    mock_process.stderr.readline = AsyncMock(side_effect=[
        b"error msg\n",
        b""
    ])
    mock_process.stderr.at_eof = MagicMock(side_effect=[False, False, True])
    client.process = mock_process
    
    await client._read_stderr()

@pytest.mark.asyncio
async def test_send_notification(client):
    # Tests lines 189-198
    client.config.debug = True
    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.stdin = MagicMock()
    mock_process.stdin.write = MagicMock()
    mock_process.stdin.drain = AsyncMock()
    client.process = mock_process
    
    with patch.object(client, 'ensure_running', new_callable=AsyncMock):
        await client.send_notification("session/cancel", {"sessionId": "s1"})
        assert mock_process.stdin.write.called
        assert mock_process.stdin.drain.called

@pytest.mark.asyncio
async def test_send_raw_request_terminate_exception(client):
    # Tests lines 231-232
    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.stdin = MagicMock()
    mock_process.stdin.write = MagicMock()
    mock_process.stdin.drain = AsyncMock()
    mock_process.terminate = MagicMock(side_effect=Exception("Failed to terminate"))
    client.process = mock_process
    
    with patch.object(client, '_send_raw_request', new_callable=AsyncMock) as mock_raw:
        # Trigger an asyncio.TimeoutError in send_request
        mock_raw.side_effect = asyncio.TimeoutError()
        with patch.object(client, 'ensure_running', new_callable=AsyncMock):
            with pytest.raises(asyncio.TimeoutError):
                await client.send_request("test")
            assert mock_process.terminate.called

@pytest.mark.asyncio
async def test_create_session_error(client):
    # Tests line 243
    with patch.object(client, 'send_request', new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"error": "Too many sessions"}
        with patch.object(client, 'ensure_running', new_callable=AsyncMock):
            with pytest.raises(Exception, match="Failed to create session"):
                await client.create_session()

@pytest.mark.asyncio
async def test_prompt_session_not_found(client):
    # Tests line 256
    with pytest.raises(ValueError, match="Session s1 not found"):
        async for _ in client.prompt("s1", "hello"):
            pass

@pytest.mark.asyncio
async def test_prompt_clears_queue_and_success_flow(client):
    # Tests lines 261, 296-297
    session_id = "s1"
    client.session_queues[session_id] = asyncio.Queue()
    # Put an item that should be cleared
    await client.session_queues[session_id].put("stale")
    
    mock_process = MagicMock()
    mock_process.returncode = None
    client.process = mock_process
    
    prompt_res_fut = asyncio.Future()
    
    async def mock_send_request(*args, **kwargs):
        return await prompt_res_fut
        
    client.send_request = mock_send_request
    
    # We start prompt, which clears the stale items and blocks waiting for send_request
    chunks = []
    
    async def run_prompt():
        async for chunk in client.prompt(session_id, "hello"):
            chunks.append(chunk)
            
    prompt_task = asyncio.create_task(run_prompt())
    # Give it a tiny sleep to execute up to blocking on asyncio.wait
    await asyncio.sleep(0.01)
    
    # Now put the chunks and set prompt_res_fut result
    await client.session_queues[session_id].put({
        "method": "session/prompt/next",
        "params": {
            "chunk": {
                "type": "text",
                "text": "hello "
            }
        }
    })
    await client.session_queues[session_id].put({
        "method": "session/prompt/next",
        "params": {
            "chunk": {
                "type": "text",
                "text": "world"
            }
        }
    })
    
    # Wait another moment to process chunks
    await asyncio.sleep(0.1)
    
    # Finish the send_request future
    prompt_res_fut.set_result({"result": "success"})
    await prompt_task
        
    assert len(chunks) == 3
    assert chunks[0] == {"type": "content", "text": "hello "}
    assert chunks[1] == {"type": "content", "text": "hello world"}
    assert chunks[2] == {"type": "final", "text": "hello world"}

@pytest.mark.asyncio
async def test_prompt_stop_reasons(client):
    # Tests stopReason values: max_turns, max_tokens, cancelled
    for stop_reason, warning_suffix in [
        ("max_turns", "\n\n⚠️ *Warning: Session reached maximum turn limit.*"),
        ("max_tokens", "\n\n⚠️ *Warning: Session reached context token limit.*"),
        ("cancelled", "\n\n🛑 *Notice: Session prompt was cancelled.*")
    ]:
        session_id = f"s_stop_{stop_reason}"
        client.session_queues[session_id] = asyncio.Queue()
        
        mock_process = MagicMock()
        mock_process.returncode = None
        client.process = mock_process
        
        prompt_res_fut = asyncio.Future()
        
        async def mock_send_request(*args, **kwargs):
            return await prompt_res_fut
            
        client.send_request = mock_send_request
        
        chunks = []
        async def run_prompt():
            async for chunk in client.prompt(session_id, "test"):
                chunks.append(chunk)
                
        prompt_task = asyncio.create_task(run_prompt())
        await asyncio.sleep(0.01)
        
        # Put a final chunk
        await client.session_queues[session_id].put({
            "method": "session/prompt/next",
            "params": {
                "chunk": {
                    "type": "text",
                    "text": "done"
                }
            }
        })
        await asyncio.sleep(0.05)
        
        prompt_res_fut.set_result({"result": {"stopReason": stop_reason}})
        await prompt_task
        
        assert len(chunks) == 2
        assert chunks[0] == {"type": "content", "text": "done"}
        assert chunks[1] == {"type": "final", "text": "done" + warning_suffix}

@pytest.mark.asyncio
async def test_prompt_result_error(client):
    # Tests line 310
    session_id = "s1"
    client.session_queues[session_id] = asyncio.Queue()
    
    prompt_res_fut = asyncio.Future()
    prompt_res_fut.set_result({"error": "Prompt failed"})
    
    async def mock_send_request(*args, **kwargs):
        return await prompt_res_fut
        
    client.send_request = mock_send_request
    
    await client.session_queues[session_id].put(None)
    
    with pytest.raises(Exception, match="Goose error: Prompt failed"):
        async for _ in client.prompt(session_id, "hello"):
            pass

@pytest.mark.asyncio
async def test_prompt_inactivity_timeout(client):
    # Tests lines 322-337
    session_id = "s1"
    client.session_queues[session_id] = asyncio.Queue()
    client.config.rpc_timeout = 0.05
    
    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.terminate = MagicMock()
    client.process = mock_process
    
    # Create an unresolved future for send_request
    prompt_res_fut = asyncio.Future()
    
    async def mock_send_request(*args, **kwargs):
        return await prompt_res_fut
        
    client.send_request = mock_send_request
    
    # Don't put anything in queue, let it block on queue.get
    with pytest.raises(asyncio.TimeoutError, match="Request session/prompt timed out"):
        async for _ in client.prompt(session_id, "hello"):
            pass
            
    assert client._healthy is False
    assert mock_process.terminate.called

@pytest.mark.asyncio
async def test_prompt_inactivity_timeout_terminate_exception(client):
    # Tests lines 333-334
    session_id = "s1"
    client.session_queues[session_id] = asyncio.Queue()
    client.config.rpc_timeout = 0.05
    
    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.terminate = MagicMock(side_effect=Exception("Failed to terminate"))
    client.process = mock_process
    
    # Create an unresolved future for send_request
    prompt_res_fut = asyncio.Future()
    
    async def mock_send_request(*args, **kwargs):
        return await prompt_res_fut
        
    client.send_request = mock_send_request
    
    # Don't put anything in queue, let it block on queue.get
    with pytest.raises(asyncio.TimeoutError, match="Request session/prompt timed out"):
        async for _ in client.prompt(session_id, "hello"):
            pass
            
    assert client._healthy is False
    assert mock_process.terminate.called

@pytest.mark.asyncio
async def test_parse_update_chunk_tool_call(client):
    # Tests line 377
    chunk = {
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "tool_call",
                "title": "Performing tool call"
            }
        }
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed == {"type": "tool", "name": "Performing tool call", "arguments": {}}

@pytest.mark.asyncio
async def test_parse_update_chunk_usage_and_debug(client):
    # Tests lines 390-400
    client.config.debug = True
    chunk = {
        "method": "session/update",
        "params": {
            "sessionId": "s1",
            "update": {
                "sessionUpdate": "usage_update",
                "used": 100,
                "size": 1000
            }
        }
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed == {"type": "usage", "used": 100, "size": 1000}
    assert client.context_usage["s1"] == {"used": 100, "size": 1000}
    
    # Test unknown format log in debug
    chunk_unknown = {
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "unknown_format"
            }
        }
    }
    parsed_unknown = client._parse_update_chunk(chunk_unknown)
    assert parsed_unknown is None

@pytest.mark.asyncio
async def test_parse_update_chunk_thought_and_title(client):
    # Tests agent_thought_chunk and session_info_update
    chunk_thought = {
        "method": "session/update",
        "params": {
            "sessionId": "s1",
            "update": {
                "sessionUpdate": "agent_thought_chunk",
                "content": {
                    "type": "text",
                    "text": "thinking hard"
                }
            }
        }
    }
    parsed_thought = client._parse_update_chunk(chunk_thought)
    assert parsed_thought == {"type": "thinking", "text": "thinking hard"}

    chunk_title = {
        "method": "session/update",
        "params": {
            "sessionId": "s1",
            "update": {
                "sessionUpdate": "session_info_update",
                "title": "New Title"
            }
        }
    }
    parsed_title = client._parse_update_chunk(chunk_title)
    assert parsed_title == {"type": "title", "title": "New Title"}

@pytest.mark.asyncio
async def test_drain_remaining_chunks_no_session(client):
    # Tests line 406
    res = await client._drain_remaining_chunks("invalid", "initial")
    assert res == "initial"

@pytest.mark.asyncio
async def test_cancel_prompt(client):
    # Tests lines 419-426
    client.active_prompts["s1"] = 100
    
    with patch.object(client, 'send_notification', new_callable=AsyncMock) as mock_notif:
        res = await client.cancel_prompt("s1")
        assert res is True
        mock_notif.assert_called_once_with("session/cancel", {"sessionId": "s1", "messageId": 100})
        
        # Test cancel on inactive session
        mock_notif.reset_mock()
        res_inactive = await client.cancel_prompt("s2")
        assert res_inactive is False
        assert not mock_notif.called

async def test_start_command_line_and_env(config):
    config.goose_provider = "anthropic"
    config.goose_anthropic_api_key = "ant-key"
    config.goose_builtin_extensions = ["developer"]
    
    client = GooseACPClient(config=config)
    
    with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec:
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_exec.return_value = mock_process
        
        # Mock initialize handshake
        with patch.object(client, '_send_raw_request', new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = {"result": {}}
            await client._start()
            
            args, kwargs = mock_exec.call_args
            cmd = args
            env = kwargs.get('env', {})
            
            assert "goose" in cmd
            assert "acp" in cmd
            assert "--with-builtin" in cmd
            assert "developer" in cmd
            assert env.get("GOOSE_PROVIDER") == "anthropic"
            assert env.get("ANTHROPIC_API_KEY") == "ant-key"

@pytest.mark.asyncio
async def test_start_with_linux_user_and_env(config):
    config.goose_provider = "openai"
    config.goose_openai_api_key = "sk-test"
    
    client = GooseACPClient(linux_user="testuser", config=config)
    
    with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec,\
         patch('pwd.getpwnam') as mock_pwd:
        
        mock_pwd.return_value.pw_dir = "/home/testuser"
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_exec.return_value = mock_process
        
        with patch.object(client, '_send_raw_request', new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = {"result": {}}
            await client._start()
            
            args, kwargs = mock_exec.call_args
            cmd = args
            
            assert "sudo" in cmd
            assert "testuser" in cmd
            assert "/usr/bin/env" in cmd
            assert "GOOSE_PROVIDER=openai" in cmd
            assert "OPENAI_API_KEY=sk-test" in cmd
            assert "goose" in cmd


@pytest.mark.asyncio
async def test_start_with_user_overrides(config, tmp_path):
    user_configs_dir = tmp_path / "user_configs"
    user_configs_dir.mkdir()
    user_file = user_configs_dir / "user1.env"
    user_file.write_text(
        "GOOSE_PROVIDER=google\n"
        "GOOGLE_API_KEY=goog-key\n"
        "CUSTOM_ENV_VAR=hello_world\n"
    )
    
    config.user_configs_dir = str(user_configs_dir)
    config.goose_provider = "openai"
    config.goose_openai_api_key = "sk-test"
    
    client = GooseACPClient(linux_user="user1", config=config)
    
    with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as mock_exec,\
         patch('pwd.getpwnam') as mock_pwd:
        
        mock_pwd.return_value.pw_dir = "/home/user1"
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_exec.return_value = mock_process
        
        with patch.object(client, '_send_raw_request', new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = {"result": {}}
            await client._start()
            
            args, kwargs = mock_exec.call_args
            cmd = args
            env = kwargs.get('env', {})
            
            # Subprocess env should have overwritten and extra variables
            assert env.get("GOOSE_PROVIDER") == "google"
            assert env.get("GOOGLE_API_KEY") == "goog-key"
            assert env.get("CUSTOM_ENV_VAR") == "hello_world"
            
            # The sudo command should pass overrides down
            assert "sudo" in cmd
            assert "user1" in cmd
            assert "/usr/bin/env" in cmd
            assert "GOOSE_PROVIDER=google" in cmd
            assert "GOOGLE_API_KEY=goog-key" in cmd
            assert "CUSTOM_ENV_VAR=hello_world" in cmd
            
            # Verify the default sk-test key is still in keys to pass (sorted output order)
            assert "OPENAI_API_KEY=sk-test" in cmd
            assert "goose" in cmd

