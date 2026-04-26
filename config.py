import os
from dotenv import load_dotenv

load_dotenv()

# Configuration from environment
MATTERMOST_URL = os.getenv("MATTERMOST_URL", "").strip().rstrip('/')
MATTERMOST_TOKEN = os.getenv("MATTERMOST_TOKEN")
MATTERMOST_SCHEME = os.getenv("MATTERMOST_SCHEME", "https")
MATTERMOST_PORT = os.getenv("MATTERMOST_PORT", "443")
APPROVED_USERS = [u.strip() for u in os.getenv("APPROVED_USERS", "").split(",") if u.strip()]
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "1"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
GOOSE_THINKING_TRACE = os.getenv("GOOSE_THINKING_TRACE", "true").lower() == "true"
RPC_TIMEOUT = int(os.getenv("RPC_TIMEOUT", "600"))
MAX_SESSIONS = int(os.getenv("MAX_SESSIONS", "100"))
USER_MAPPING_FILE = os.getenv("USER_MAPPING_FILE", "user_mapping.json")
REQUIRE_USER_MAPPING = os.getenv("REQUIRE_USER_MAPPING", "false").lower() == "true"
