from utils import clean_message, get_session_key, load_user_mapping
import json
import os

def test_clean_message():
    assert clean_message("@bot hello", "@bot") == "hello"
    assert clean_message("@bot: hello", "@bot") == "hello"
    assert clean_message("@bot, hello", "@bot") == "hello"
    assert clean_message("just a message", "@bot") == "just a message"

def test_get_session_key():
    assert get_session_key("user1", "root1") == "user1:root1"

def test_load_user_mapping(tmp_path):
    mapping_file = tmp_path / "mapping.json"
    data = {"user1": "linux_user1"}
    mapping_file.write_text(json.dumps(data))
    
    assert load_user_mapping(str(mapping_file)) == data
    assert load_user_mapping("non_existent.json") == {}
