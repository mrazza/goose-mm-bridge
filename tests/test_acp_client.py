import asyncio
import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from config import Config
from acp_client import ACPClient


@pytest.fixture
def config():
    return Config(rpc_timeout=1)


@pytest.fixture
async def client(config):
    # Patch subprocess globally for all tests to prevent accidental spawns
    with patch('asyncio.create_subprocess_exec',
               new_callable=AsyncMock) as mock_exec:
        client = ACPClient(config=config)
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


@pytest.mark.asyncio
async def test_ensure_running_restart(client):
    mock_process = MagicMock()
    mock_process.returncode = None
    client.process = mock_process

    # Mock process died
    mock_process.returncode = 1

    with patch('asyncio.create_subprocess_exec',
               new_callable=AsyncMock) as mock_exec:
        new_process = MagicMock()
        new_process.returncode = None
        mock_exec.return_value = new_process

        with patch.object(client, '_send_raw_request',
                           new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = {"result": {}}
            await client.ensure_running()

            assert mock_exec.called
            assert client.process == new_process


@pytest.mark.asyncio
async def test_ensure_running_healthy_flag(client):
    mock_process = MagicMock()
    mock_process.returncode = None
    client.process = mock_process
    client._healthy = False  # Trigger restart due to unhealthiness

    with patch('asyncio.create_subprocess_exec',
               new_callable=AsyncMock) as mock_exec:
        new_process = MagicMock()
        new_process.returncode = None
        mock_exec.return_value = new_process

        with patch.object(client, '_send_raw_request',
                           new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = {"result": {}}
            await client.ensure_running()

            assert mock_exec.called
            assert client.process == new_process
            assert client._healthy is True


@pytest.mark.asyncio
async def test_ensure_running_fails_pending_requests(client):
    # Set up pending requests
    loop = asyncio.get_running_loop()
    f1 = loop.create_future()
    f2 = loop.create_future()
    client.pending_requests = {1: f1, 2: f2}

    # Simulate process died
    client.process = MagicMock()
    client.process.returncode = 1

    with patch('asyncio.create_subprocess_exec',
               new_callable=AsyncMock) as mock_exec:
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_exec.return_value = mock_process

        with patch.object(client, '_send_raw_request',
                           new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = {"result": {}}
            await client.ensure_running()

            assert f1.done()
            assert f2.done()
            with pytest.raises(RuntimeError, match="ACP process terminated"):
                f1.result()


@pytest.mark.asyncio
async def test_prompt_success(client):
    # Mock send_request and session_queues
    session_id = "s1"
    queue = asyncio.Queue()
    client.session_queues[session_id] = queue

    # Ensure process is not None to avoid "ACP process terminated" check
    mock_process = MagicMock()
    mock_process.returncode = None
    client.process = mock_process

    # Final response future
    res_future = asyncio.Future()
    res_future.set_result({"result": {"stopReason": "complete"}})

    async def mock_send_request(*args, **kwargs):
        # Populate queue after the prompt starts and clears the queue
        await queue.put({
            "method": "session/update",
            "params": {
                "sessionId": session_id,
                "update": {
                    "sessionUpdate": "agent_thinking_chunk",
                    "thinking": "analyzing..."
                }
            }
        })
        await queue.put({
            "method": "session/prompt/next",
            "params": {
                "sessionId": session_id,
                "chunk": {
                    "type": "text",
                    "text": "Hello"
                }
            }
        })
        await queue.put({
            "method": "session/prompt/next",
            "params": {
                "sessionId": session_id,
                "chunk": {
                    "type": "text",
                    "text": " world"
                }
            }
        })
        # Wait a small delay to let the generator process chunks before we return the final response
        await asyncio.sleep(0.05)
        return await res_future

    # Mock send_request for session/prompt to return the final response
    with patch.object(client, 'send_request', side_effect=mock_send_request):
        updates = []
        async for u in client.prompt(session_id, "hi"):
            updates.append(u)

        # Check updates yielded
        assert len(updates) >= 4
        # Filter for content chunks
        content_updates = [
            u for u in updates if u["type"] == "content"
        ]
        assert content_updates[-1]["text"] == "Hello world"
        assert updates[-1] == {"type": "final", "text": "Hello world"}


@pytest.mark.asyncio
async def test_prompt_inactivity_timeout(client):
    session_id = "s1"
    client.session_queues[session_id] = asyncio.Queue()
    client.config.rpc_timeout = 0.05  # Very short timeout

    # Never resolve prompt
    res_future = asyncio.Future()

    mock_process = MagicMock()
    mock_process.returncode = None
    client.process = mock_process

    async def mock_send_request(*args, **kwargs):
        return await res_future

    with patch.object(client, 'send_request', side_effect=mock_send_request):
        with pytest.raises(asyncio.TimeoutError):
            async for _ in client.prompt(session_id, "hi"):
                pass

        assert client._healthy is False
        assert mock_process.terminate.called


@pytest.mark.asyncio
async def test_prompt_process_terminated_error(client):
    session_id = "s1"
    client.session_queues[session_id] = asyncio.Queue()

    # Never resolve prompt
    res_future = asyncio.Future()

    client.process = MagicMock()
    # Simulate process died immediately during loop
    client.process.returncode = 1

    async def mock_send_request(*args, **kwargs):
        return await res_future

    with patch.object(client, 'send_request', side_effect=mock_send_request):
        with pytest.raises(RuntimeError, match="ACP process terminated during prompt"):
            async for _ in client.prompt(session_id, "hi"):
                pass


@pytest.mark.asyncio
async def test_prompt_clears_queue(client):
    session_id = "s1"
    queue = asyncio.Queue()
    client.session_queues[session_id] = queue

    # Ensure process is not None to avoid "ACP process terminated" check
    mock_process = MagicMock()
    mock_process.returncode = None
    client.process = mock_process

    # Put a stale chunk in the queue
    await queue.put({
        "method": "session/prompt/next",
        "params": {
            "sessionId": session_id,
            "chunk": {
                "type": "text",
                "text": "stale"
            }
        }
    })

    # Immediate final response
    res_future = asyncio.Future()
    res_future.set_result({"result": {"stopReason": "complete"}})

    async def mock_send_request(*args, **kwargs):
        return await res_future

    with patch.object(client, 'send_request', side_effect=mock_send_request):
        updates = []
        async for u in client.prompt(session_id, "hi"):
            updates.append(u)

        # Stale chunk should be cleared, full_response should be empty
        assert updates[-1] == {"type": "final", "text": ""}


@pytest.mark.asyncio
async def test_prompt_stop_reason_warnings(client):
    session_id = "s1"
    client.session_queues[session_id] = asyncio.Queue()

    # Ensure process is not None to avoid "ACP process terminated" check
    mock_process = MagicMock()
    mock_process.returncode = None
    client.process = mock_process

    # Create helper to test different stop reasons
    async def run_with_stop_reason(reason):
        res_future = asyncio.Future()
        res_future.set_result({"result": {"stopReason": reason}})
        
        async def mock_send_request(*args, **kwargs):
            return await res_future

        with patch.object(client, 'send_request', side_effect=mock_send_request):
            updates = []
            async for u in client.prompt(session_id, "hi"):
                updates.append(u)
            return updates[-1]["text"]

    t1 = await run_with_stop_reason("max_turns")
    assert "Warning: Session reached maximum turn limit." in t1

    t2 = await run_with_stop_reason("max_tokens")
    assert "Warning: Session reached context token limit." in t2

    t3 = await run_with_stop_reason("cancelled")
    assert "Notice: Session prompt was cancelled." in t3


@pytest.mark.asyncio
async def test_read_stdout_closes_pending_requests(client):
    loop = asyncio.get_running_loop()
    f1 = loop.create_future()
    client.pending_requests = {1: f1}

    # Set up stdout stream mock
    mock_stdout = MagicMock()
    mock_stdout.readline = AsyncMock(return_value=b"")  # EOF
    mock_stdout.at_eof = MagicMock(return_value=True)

    client.process = MagicMock()
    client.process.stdout = mock_stdout

    # Run read loop tasks which should exit immediately on EOF
    await client._read_stdout()

    assert f1.done()
    with pytest.raises(RuntimeError, match="Agent ACP stdout closed"):
        f1.result()


@pytest.mark.asyncio
async def test_read_stdout_processes_responses(client):
    loop = asyncio.get_running_loop()
    f1 = loop.create_future()
    client.pending_requests = {10: f1}

    # Simulate receiving response from subprocess stdout
    mock_stdout = MagicMock()
    mock_stdout.readline = AsyncMock(side_effect=[
        b'{"id": 10, "result": "done"}\n',
        b""  # EOF
    ])
    mock_stdout.at_eof = MagicMock(side_effect=[False, False, True])

    client.process = MagicMock()
    client.process.stdout = mock_stdout

    await client._read_stdout()

    assert f1.done()
    assert f1.result() == {"id": 10, "result": "done"}


@pytest.mark.asyncio
async def test_read_stdout_processes_session_updates(client):
    session_id = "s1"
    queue = asyncio.Queue()
    client.session_queues[session_id] = queue

    mock_stdout = MagicMock()
    mock_stdout.readline = AsyncMock(side_effect=[
        b'{"method": "session/update", "params": {"sessionId": "s1", "update": {"sessionUpdate": "agent_thinking_chunk"}}}\n',
        b""  # EOF
    ])
    mock_stdout.at_eof = MagicMock(side_effect=[False, False, True])

    client.process = MagicMock()
    client.process.stdout = mock_stdout

    await client._read_stdout()

    assert not queue.empty()
    item = queue.get_nowait()
    assert item["method"] == "session/update"
    assert item["params"]["sessionId"] == "s1"


@pytest.mark.asyncio
async def test_read_stdout_ignores_empty_or_malformed(client):
    mock_stdout = MagicMock()
    mock_stdout.readline = AsyncMock(side_effect=[
        b"\n",  # Empty line
        b"not json\n",  # Malformed JSON
        b""  # EOF
    ])
    mock_stdout.at_eof = MagicMock(side_effect=[False, False, False, True])

    client.process = MagicMock()
    client.process.stdout = mock_stdout

    # Should run to completion without raising exception
    await client._read_stdout()


@pytest.mark.asyncio
async def test_read_stderr(client):
    # Capture print outputs
    mock_stderr = MagicMock()
    mock_stderr.readline = AsyncMock(side_effect=[
        b"error trace line 1\n",
        b"error trace line 2\n",
        b""  # EOF
    ])
    mock_stderr.at_eof = MagicMock(side_effect=[False, False, True])

    client.process = MagicMock()
    client.process.stderr = mock_stderr

    # Should run to completion
    await client._read_stderr()


@pytest.mark.asyncio
async def test_send_raw_request(client):
    # Setup mock stdin
    mock_stdin = MagicMock()
    mock_stdin.write = MagicMock()
    mock_stdin.drain = AsyncMock()

    client.process = MagicMock()
    client.process.stdin = mock_stdin

    # Mock response after sending request
    async def mock_send():
        # Wait a tick then simulate stdout response
        await asyncio.sleep(0.01)
        client.pending_requests[client.last_id_used].set_result({"id": client.last_id_used, "result": "ok"})

    asyncio.create_task(mock_send())
    res = await client._send_raw_request("method_a", {"x": 1})

    assert res == {"id": client.last_id_used, "result": "ok"}
    # Verify stdin write format
    assert mock_stdin.write.called
    written_data = mock_stdin.write.call_args[0][0].decode()
    req = json.loads(written_data)
    assert req["method"] == "method_a"
    assert req["params"] == {"x": 1}
    assert req["id"] == client.last_id_used


@pytest.mark.asyncio
async def test_send_raw_request_cleanup_on_cancel(client):
    # Setup mock stdin
    mock_stdin = MagicMock()
    mock_stdin.write = MagicMock()
    mock_stdin.drain = AsyncMock()

    client.process = MagicMock()
    client.process.stdin = mock_stdin

    async def call_and_cancel():
        t = asyncio.create_task(client._send_raw_request("method_a"))
        await asyncio.sleep(0.01)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    await call_and_cancel()
    # verify cleanup of pending request future
    assert len(client.pending_requests) == 0


@pytest.mark.asyncio
async def test_send_notification(client):
    mock_stdin = MagicMock()
    mock_stdin.write = MagicMock()
    mock_stdin.drain = AsyncMock()

    client.process = MagicMock()
    client.process.stdin = mock_stdin

    with patch.object(client, 'ensure_running', new_callable=AsyncMock) as mock_running:
        await client.send_notification("notif_method", {"y": 2})

        assert mock_running.called
        assert mock_stdin.write.called
        written_data = mock_stdin.write.call_args[0][0].decode()
        req = json.loads(written_data)
        assert req["method"] == "notif_method"
        assert req["params"] == {"y": 2}
        assert "id" not in req  # Notification must not have an ID


@pytest.mark.asyncio
async def test_create_session(client):
    with patch.object(client, 'ensure_running', new_callable=AsyncMock),\
         patch.object(client, 'send_request', new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"result": {"sessionId": "new_session_123"}}

        sid = await client.create_session()
        assert sid == "new_session_123"
        assert "new_session_123" in client.session_queues
        mock_request.assert_called_once()
        assert mock_request.call_args[0][0] == "session/new"


@pytest.mark.asyncio
async def test_create_session_error(client):
    with patch.object(client, 'ensure_running', new_callable=AsyncMock),\
         patch.object(client, 'send_request', new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"error": {"message": "session limit reached"}}

        with pytest.raises(Exception, match="Failed to create session: .*session limit reached"):
            await client.create_session()


def test_parse_update_chunk_various(client):
    # Test sessionUpdate call_tool
    chunk = {
        "params": {
            "update": {
                "sessionUpdate": "call_tool",
                "toolCall": {
                    "name": "read_file",
                    "arguments": {"path": "a.txt"}
                }
            }
        }
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed == {"type": "tool", "name": "read_file", "arguments": {"path": "a.txt"}}

    # Test sessionUpdate tool_call
    chunk = {
        "params": {
            "update": {
                "sessionUpdate": "tool_call",
                "title": "fetching details"
            }
        }
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed == {"type": "tool", "name": "fetching details", "arguments": {}}

    # Test sessionUpdate tool_call_update
    chunk = {
        "params": {
            "update": {
                "sessionUpdate": "tool_call_update",
                "title": "working hard"
            }
        }
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed == {"type": "thinking", "text": "working hard"}

    # Test sessionUpdate usage_update
    chunk = {
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

    # Test sessionUpdate session_info_update
    chunk = {
        "params": {
            "update": {
                "sessionUpdate": "session_info_update",
                "title": "New Topic"
            }
        }
    }
    parsed = client._parse_update_chunk(chunk)
    assert parsed == {"type": "title", "title": "New Topic"}

    # Test unknown chunk
    chunk = {"params": {}}
    parsed = client._parse_update_chunk(chunk)
    assert parsed is None


@pytest.mark.asyncio
async def test_cancel_prompt(client):
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


@pytest.mark.asyncio
async def test_start_command_line_and_env(config):
    config.goose_provider = "anthropic"
    config.goose_anthropic_api_key = "ant-key"
    config.goose_builtin_extensions = ["developer"]

    client = ACPClient(config=config)

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

    client = ACPClient(linux_user="testuser", config=config)

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

    client = ACPClient(linux_user="user1", config=config)

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
