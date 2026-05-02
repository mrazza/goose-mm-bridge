import json
import os

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
