import json
import os
from datetime import datetime

def clean_message(message: str, bot_mention: str) -> str:
    """Removes the bot mention and leading punctuation from a message."""
    if bot_mention in message:
        message = message.replace(bot_mention, "").strip()
        if message.startswith(",") or message.startswith(":"):
            message = message[1:].strip()
    return message

def load_user_mapping(mapping_file: str) -> dict:
    """Loads the user mapping from the JSON file."""
    if os.path.exists(mapping_file):
        try:
            with open(mapping_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[{datetime.now()}] Error loading user mapping: {e}")
    return {}

def get_session_key(user_id: str, root_id: str) -> str:
    """Returns a unique session key based on user and thread."""
    return f"{user_id}:{root_id}"
