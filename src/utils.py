from datetime import datetime
import json
import os
from typing import Optional


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


def load_prewarm_config(config_file: str, prewarm_users_str: Optional[str] = None) -> dict:
    """Loads pre-warm configurations from a JSON file and/or PREWARM_USERS string."""
    result = {}
    if config_file:
        path = config_file
        if not os.path.isabs(path):
            from config import project_root
            path = os.path.join(project_root, path)
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if isinstance(v, list):
                                result[k] = [str(x).strip() for x in v if str(x).strip()]
                            elif isinstance(v, str) and v.strip():
                                result[k] = [v.strip()]
            except Exception as e:
                print(f"[{datetime.now()}] Error loading prewarm config from {path}: {e}")

    if prewarm_users_str:
        for item in prewarm_users_str.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                user, agents = item.split(":", 1)
                user = user.strip()
                agent_list = [a.strip() for a in agents.replace("+", ",").split(",") if a.strip()]
                if user and agent_list:
                    if user in result:
                        for a in agent_list:
                            if a not in result[user]:
                                result[user].append(a)
                    else:
                        result[user] = agent_list
            else:
                user = item
                if user not in result:
                    result[user] = ["default"]

    return result
