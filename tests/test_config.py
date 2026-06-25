import os
from unittest.mock import patch

from config import Config


def test_config_defaults():
    config = Config(mattermost_url="example.com", mattermost_token="token")
    assert config.mattermost_url == "example.com"
    assert config.mattermost_token == "token"
    assert config.mattermost_scheme == "https"
    assert config.poll_interval == 1


def test_config_approved_users():
    with patch.dict(os.environ, {"APPROVED_USERS": "user1, user2"}):
        # We pass None to trigger the __post_init__ logic that reads from env
        config = Config(approved_users=None)
        assert config.approved_users == ["user1", "user2"]

def test_config_initialization():
    config = Config(
        mattermost_url="test.com",
        mattermost_token="secret",
        mattermost_scheme="http",
        mattermost_port="80",
        poll_interval=5,
        debug=True,
        rpc_timeout=30,
        max_sessions=10,
        require_user_mapping=True,
        mcp_enabled=False
    )
    assert config.mattermost_url == "test.com"
    assert config.mattermost_token == "secret"
    assert config.mattermost_scheme == "http"
    assert config.mattermost_port == "80"
    assert config.poll_interval == 5
    assert config.debug is True
    assert config.rpc_timeout == 30
    assert config.max_sessions == 10
    assert config.require_user_mapping is True
    assert config.mcp_enabled is False

def test_config_approved_users_empty():
    with patch.dict(os.environ, {"APPROVED_USERS": ""}):
        config = Config(approved_users=None)
        assert config.approved_users == []

def test_config_goose_extensions_and_mcps():
    env = {
        "GOOSE_BUILTIN_EXTENSIONS": "developer, summarize",
        "GOOSE_MCP_SERVERS": '[{"name": "test", "type": "stdio"}]'
    }
    with patch.dict(os.environ, env):
        config = Config(goose_builtin_extensions=None, goose_mcp_servers=None)
        assert config.goose_builtin_extensions == ["developer", "summarize"]
        assert len(config.goose_mcp_servers) == 1
        assert config.goose_mcp_servers[0]["name"] == "test"

def test_config_goose_env_vars():
    env = {
        "GOOSE_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-test"
    }
    with patch.dict(os.environ, env):
        config = Config(goose_provider=None, goose_openai_api_key=None)
        assert config.goose_provider == "openai"
        assert config.goose_openai_api_key == "sk-test"


def test_load_user_config_no_file():
    from config import load_user_config
    config = Config()
    config.user_configs_dir = "/nonexistent_dir"
    loaded = load_user_config("nonexistent_user", config)
    assert loaded is config


def test_load_user_config_with_file(tmp_path):
    from config import load_user_config
    user_dir = tmp_path / "user_configs"
    user_dir.mkdir()
    user_file = user_dir / "test_user.env"
    user_file.write_text(
        "GOOSE_PROVIDER=anthropic\n"
        "GOOSE_MODEL=claude-3-5-sonnet\n"
        "OPENAI_API_KEY=sk-user-test\n"
        "RPC_TIMEOUT=120\n"
        "DEBUG=true\n"
        "GOOSE_BUILTIN_EXTENSIONS=developer,web_scrape\n"
        "GOOSE_MCP_SERVERS=[{\"name\": \"user-mcp\", \"type\": \"stdio\"}]\n"
        "CUSTOM_VAR=custom_val\n"
    )

    config = Config()
    config.user_configs_dir = str(user_dir)
    loaded = load_user_config("test_user", config)

    assert loaded.goose_provider == "anthropic"
    assert loaded.goose_model == "claude-3-5-sonnet"
    assert loaded.goose_openai_api_key == "sk-user-test"
    assert loaded.rpc_timeout == 120
    assert loaded.debug is True
    assert loaded.goose_builtin_extensions == ["developer", "web_scrape"]
    assert len(loaded.goose_mcp_servers) == 1
    assert loaded.goose_mcp_servers[0]["name"] == "user-mcp"
    assert loaded.user_env_vars == {
        "GOOSE_PROVIDER": "anthropic",
        "GOOSE_MODEL": "claude-3-5-sonnet",
        "OPENAI_API_KEY": "sk-user-test",
        "RPC_TIMEOUT": "120",
        "DEBUG": "true",
        "GOOSE_BUILTIN_EXTENSIONS": "developer,web_scrape",
        "GOOSE_MCP_SERVERS": '[{"name": "user-mcp", "type": "stdio"}]',
        "CUSTOM_VAR": "custom_val"
    }

