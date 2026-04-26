import json
import os
from datetime import datetime
from config import USER_MAPPING_FILE

def clean_message(message: str, bot_mention: str) -> str:
    """Removes the bot mention and leading punctuation from a message."""
    if bot_mention in message:
        message = message.replace(bot_mention, "").strip()
        if message.startswith(",") or message.startswith(":"):
            message = message[1:].strip()
    return message

def load_user_mapping() -> dict:
    """Loads the user mapping from the JSON file."""
    if os.path.exists(USER_MAPPING_FILE):
        try:
            with open(USER_MAPPING_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[{datetime.now()}] Error loading user mapping: {e}")
    return {}
