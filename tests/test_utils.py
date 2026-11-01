import json
import os
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st
import pytest

from utils import clean_message
from utils import get_session_key
from utils import load_user_mapping


def test_clean_message():
    assert clean_message("@bot hello", "@bot") == "hello"
    assert clean_message("@bot: hello", "@bot") == "hello"
    assert clean_message("@bot, hello", "@bot") == "hello"
    assert clean_message("just a message", "@bot") == "just a message"


@given(st.text(), st.text(min_size=1))
def test_clean_message_property(msg, bot_name):
    # Ensure no crash and basic property: if bot name is in msg, it should be removed
    # (Simplified property since clean_message has specific logic for prefix punctuation)
    result = clean_message(msg, bot_name)
    assert isinstance(result, str)
    if bot_name in msg and msg.strip().startswith(bot_name):
        assert bot_name not in result


def test_get_session_key():
    assert get_session_key("user1", "root1") == "user1:root1"


def test_load_user_mapping(tmp_path):
    mapping_file = tmp_path / "mapping.json"
    data = {"user1": "linux_user1"}
    mapping_file.write_text(json.dumps(data))

    assert load_user_mapping(str(mapping_file)) == data
    assert load_user_mapping("non_existent.json") == {}


def test_load_user_mapping_exception(tmp_path):
    # Test line 21-22 error handling
    mapping_file = tmp_path / "mapping.json"
    mapping_file.write_text("{invalid json}")
    assert load_user_mapping(str(mapping_file)) == {}


def test_load_prewarm_config(tmp_path):
    from utils import load_prewarm_config
    config_file = tmp_path / "prewarm.json"
    data = {
        "user1": ["hermes"],
        "user2": ["goose", "hermes"],
        "user3": "default"
    }
    config_file.write_text(json.dumps(data))

    loaded = load_prewarm_config(str(config_file))
    assert loaded == {
        "user1": ["hermes"],
        "user2": ["goose", "hermes"],
        "user3": ["default"]
    }

    # With prewarm_users shorthand override/supplement
    loaded_with_env = load_prewarm_config(str(config_file), "user1:goose,user4:hermes+default,user5")
    assert "hermes" in loaded_with_env["user1"]
    assert "goose" in loaded_with_env["user1"]
    assert loaded_with_env["user4"] == ["hermes", "default"]
    assert loaded_with_env["user5"] == ["default"]

    # Relative path test
    rel_file = tmp_path / "rel_prewarm.json"
    rel_file.write_text('{"rel_user": ["hermes"]}')
    with patch("config.project_root", str(tmp_path)):
        assert load_prewarm_config("rel_prewarm.json") == {"rel_user": ["hermes"]}

    # Invalid JSON
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text("{invalid")
    assert load_prewarm_config(str(invalid_file), None) == {}
