import asyncio
import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from acp.client import ACPClient
from acp.events import parse_update_chunk
from acp.launcher import build_agent_launch_spec
from acp.pool import AgentClientPool
from config import Config
from user_store import UserStore


def test_build_agent_launch_spec_extensions_env_and_sudo():
    config = Config(
        mattermost_url="example.com",
        mattermost_token="tok",
        goose_model="claude-3-5",
        goose_builtin_extensions=["developer", "memory"],
        user_env_vars={"EXTRA_TOKEN": "secret"},
        agents={
            "goose": {
                "command": ["goose", "acp"],
                "forward_env": ["GOOSE_MODEL"],
                "env": {
                    "GOOSE_MODE": "auto"
                },
            },
            "custom": {
                "command": ["custom-cli", "--acp"],
                "extensions_flag": "--ext",
            },
        },
    )

    pw = MagicMock()
    pw.pw_dir = "/home/linux_alice"
    with patch("pwd.getpwnam", return_value=pw):
        cmd, env = build_agent_launch_spec(
            "goose",
            config,
            linux_user="linux_alice",
            base_env={"PATH": "/usr/bin"},
        )
    assert cmd[:7] == [
        "sudo", "-n", "-u", "linux_alice", "-D", "/home/linux_alice",
        "/usr/bin/env"
    ]
    assert "EXTRA_TOKEN=secret" in cmd
    assert "GOOSE_MODEL=claude-3-5" in cmd
    assert "GOOSE_MODE=auto" in cmd
    assert cmd[-4:] == ["goose", "acp", "--with-builtin", "developer,memory"]
    assert env["GOOSE_MODEL"] == "claude-3-5"

    # Custom extensions_flag without sudo + KeyError fallback
    cmd_custom, _ = build_agent_launch_spec("custom", config, linux_user=None)
    assert cmd_custom == ["custom-cli", "--acp", "--ext", "developer,memory"]

    with patch("pwd.getpwnam", side_effect=KeyError("missing")):
        cmd_fallback, _ = build_agent_launch_spec(
            "custom",
            config,
            linux_user="missing_user",
        )
    assert cmd_fallback[:5] == [
        "sudo", "-n", "-u", "missing_user", "/usr/bin/env"
    ]


def test_build_agent_launch_spec_errors():
    config = Config(
        mattermost_url="example.com",
        mattermost_token="tok",
        agents={"broken": {
            "command": []
        }},
    )
    with pytest.raises(ValueError, match="not configured"):
        build_agent_launch_spec("unknown", config)
    with pytest.raises(ValueError, match="No command configured"):
        build_agent_launch_spec("broken", config)


@pytest.mark.parametrize(
    "chunk,expected",
    [
        ({
            "params": {
                "chunk": {
                    "type": "text",
                    "text": "hi"
                }
            }
        }, {
            "type": "content",
            "text": "hi"
        }),
        ({
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "text",
                        "text": "hello"
                    },
                }
            }
        }, {
            "type": "content",
            "text": "hello"
        }),
        ({
            "params": {
                "update": {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {
                        "type": "text",
                        "text": "pondering"
                    },
                }
            }
        }, {
            "type": "thinking",
            "text": "pondering"
        }),
        ({
            "params": {
                "update": {
                    "sessionUpdate": "agent_thinking_chunk",
                    "thinking": "legacy"
                }
            }
        }, {
            "type": "thinking",
            "text": "legacy"
        }),
        ({
            "params": {
                "update": {
                    "sessionUpdate": "call_tool",
                    "toolCall": {
                        "name": "grep",
                        "arguments": {
                            "q": "x"
                        }
                    },
                }
            }
        }, {
            "type": "tool",
            "name": "grep",
            "arguments": {
                "q": "x"
            }
        }),
        ({
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "title": "shell"
                }
            }
        }, {
            "type": "tool",
            "name": "shell",
            "arguments": {}
        }),
        ({
            "params": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "title": "Running tests"
                }
            }
        }, {
            "type": "thinking",
            "text": "Running tests"
        }),
        ({
            "params": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "status": "completed"
                }
            }
        }, {
            "type": "thinking",
            "text": "working..."
        }),
        ({
            "params": {
                "update": {
                    "sessionUpdate": "usage_update",
                    "used": 10,
                    "size": 100
                }
            }
        }, {
            "type": "usage",
            "used": 10,
            "size": 100
        }),
        ({
            "params": {
                "update": {
                    "sessionUpdate": "session_info_update",
                    "title": "My Task"
                }
            }
        }, {
            "type": "title",
            "title": "My Task"
        }),
        ({
            "params": {
                "update": {
                    "sessionUpdate": "unknown_evt"
                }
            }
        }, None),
    ],
)
def test_parse_update_chunk_variants(chunk, expected):
    assert parse_update_chunk(chunk, debug=True) == expected


@pytest.mark.asyncio
async def test_acp_client_close_session_and_terminate():
    client = ACPClient(
        config=Config(mattermost_url="example.com", mattermost_token="tok"))
    client.session_queues["s1"] = asyncio.Queue()
    client.session_models["s1"] = {"current_model": "m1"}
    client.context_usage["s1"] = {"used": 1, "size": 2}
    client.active_prompts["s1"] = 7
    client.cancel_prompt = AsyncMock()
    client.send_request = AsyncMock(return_value={})

    await client.close_session("s1", await_rpc=True, cancel_first=True)
    assert "s1" not in client.session_queues
    assert "s1" not in client.session_models
    assert "s1" not in client.context_usage
    assert "s1" not in client.active_prompts
    client.cancel_prompt.assert_awaited_once_with("s1")
    client.send_request.assert_awaited_once_with("session/close",
                                                 {"sessionId": "s1"})

    # Terminate kills subprocess when wait times out
    proc = MagicMock()
    proc.returncode = None

    async def slow_wait():
        await asyncio.sleep(5.0)

    proc.wait = slow_wait
    client.process = proc
    await client.terminate(timeout=0.01)
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_agent_client_pool_prewarm_and_fault_tolerance(tmp_path):
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps({
        "u1": "linux_alice",
        "u2": "linux_bob"
    }))
    prewarm_path = tmp_path / "prewarm.json"
    prewarm_path.write_text(
        json.dumps({
            "u1": ["default", "unknown_agent"],
            "u2": ["hermes"],
            "unmapped": ["goose"],
        }))
    prefs_path = tmp_path / "prefs.json"
    prefs_path.write_text(json.dumps({"u1": "hermes"}))

    config = Config(
        mattermost_url="example.com",
        mattermost_token="tok",
        default_agent="goose",
        require_user_mapping=True,
        user_mapping_file=str(mapping_path),
        prewarm_config_file=str(prewarm_path),
        user_agent_preferences_file=str(prefs_path),
        agents={
            "goose": {
                "command": ["goose", "acp"]
            },
            "hermes": {
                "command": ["hermes", "acp"]
            },
        },
    )
    user_store = UserStore(config, MagicMock())
    created = {}

    def factory(user, agent):
        c = MagicMock()
        c.terminate = AsyncMock()
        if user == "linux_bob":
            c.ensure_running = AsyncMock(
                side_effect=RuntimeError("hermes down"))
        else:
            c.ensure_running = AsyncMock()
        created[(user, agent)] = c
        return c

    pool = AgentClientPool(config, client_factory=factory)
    await pool.prewarm(user_store)

    assert ("linux_alice", "hermes") in pool.clients  # "default" -> hermes
    assert ("linux_alice", "goose") in pool.clients  # unknown fallback -> goose
    assert ("linux_bob", "hermes") in pool.clients  # error swallowed cleanly
    assert not any(u == "unmapped" for u, _ in pool.clients)
    assert pool.find_client("linux_alice",
                            "hermes") is created[("linux_alice", "hermes")]

    await pool.terminate_all()
    for c in created.values():
        c.terminate.assert_awaited_once()
