import json
import os
from typing import Optional

from logging_config import get_logger

logger = get_logger(__name__)


def clean_message(message: str, bot_mention: Optional[str]) -> str:
    """Removes the bot mention and leading punctuation from a message."""
    if bot_mention and bot_mention in message:
        message = message.replace(bot_mention, "").strip()
        if message.startswith(",") or message.startswith(":"):
            message = message[1:].strip()
    return message


def load_user_mapping(mapping_file: str) -> dict:
    """Loads the user mapping from the JSON file."""
    if os.path.exists(mapping_file):
        try:
            with open(mapping_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error loading user mapping from %s: %s", mapping_file,
                         e)
    return {}


def get_session_key(user_id: str, root_id: str) -> str:
    """Returns a unique session key based on user and thread."""
    return f"{user_id}:{root_id}"


def _load_prewarm_file(config_file: str) -> dict:
    result = {}
    if not config_file:
        return result

    path = config_file
    if not os.path.isabs(path):
        from config import \
            project_root  # pylint: disable=import-outside-toplevel
        path = os.path.join(project_root, path)
    if not os.path.exists(path):
        return result

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error loading prewarm config from %s: %s", path, e)
        return result

    if not isinstance(data, dict):
        return result

    for k, v in data.items():
        if isinstance(v, list):
            result[k] = [str(x).strip() for x in v if str(x).strip()]
        elif isinstance(v, str) and v.strip():
            result[k] = [v.strip()]
    return result


def _merge_prewarm_shorthand(result: dict, prewarm_users_str: str) -> None:
    for item in prewarm_users_str.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            if item not in result:
                result[item] = ["default"]
            continue

        user, agents = item.split(":", 1)
        user = user.strip()
        agent_list = [
            a.strip() for a in agents.replace("+", ",").split(",") if a.strip()
        ]
        if not user or not agent_list:
            continue

        existing = result.setdefault(user, [])
        for agent in agent_list:
            if agent not in existing:
                existing.append(agent)


def load_prewarm_config(config_file: str,
                        prewarm_users_str: Optional[str] = None) -> dict:
    """Loads pre-warm configurations from a JSON file and/or env shorthand."""
    result = _load_prewarm_file(config_file)
    if prewarm_users_str:
        _merge_prewarm_shorthand(result, prewarm_users_str)
    return result
