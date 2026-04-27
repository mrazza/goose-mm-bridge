import os
from dataclasses import dataclass
from typing import List
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    mattermost_url: str = os.getenv("MATTERMOST_URL", "").strip().rstrip('/')
    mattermost_token: str = os.getenv("MATTERMOST_TOKEN")
    mattermost_scheme: str = os.getenv("MATTERMOST_SCHEME", "https")
    mattermost_port: str = os.getenv("MATTERMOST_PORT", "443")
    approved_users: List[str] = None
    poll_interval: int = int(os.getenv("POLL_INTERVAL", "1"))
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    goose_thinking_trace: bool = os.getenv("GOOSE_THINKING_TRACE", "true").lower() == "true"
    rpc_timeout: int = int(os.getenv("RPC_TIMEOUT", "600"))
    max_sessions: int = int(os.getenv("MAX_SESSIONS", "100"))
    user_mapping_file: str = os.getenv("USER_MAPPING_FILE", "user_mapping.json")
    require_user_mapping: bool = os.getenv("REQUIRE_USER_MAPPING", "false").lower() == "true"

    def __post_init__(self):
        if self.approved_users is None:
            self.approved_users = [u.strip() for u in os.getenv("APPROVED_USERS", "").split(",") if u.strip()]

# Default instance
default_config = Config()
