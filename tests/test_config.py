from config import Config
import os
from unittest.mock import patch

def test_config_defaults():
    config = Config(mattermost_url="example.com", mattermost_token="token")
    assert config.mattermost_url == "example.com"
    assert config.mattermost_token == "token"
    assert config.mattermost_scheme == "https"
    assert config.poll_interval == 1

def test_config_approved_users():
    with patch.dict(os.environ, {"APPROVED_USERS": "user1, user2"}):
        config = Config()
        assert config.approved_users == ["user1", "user2"]
