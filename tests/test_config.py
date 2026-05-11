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
