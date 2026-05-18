from dataclasses import dataclass
import os
from typing import List, Optional

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
    goose_thinking_trace: bool = os.getenv("GOOSE_THINKING_TRACE",
                                           "true").lower() == "true"
    goose_thinking_trace_simplified: bool = os.getenv(
        "GOOSE_THINKING_TRACE_SIMPLIFIED", "true").lower() == "true"
    rpc_timeout: int = int(os.getenv("RPC_TIMEOUT", "600"))
    max_sessions: int = int(os.getenv("MAX_SESSIONS", "100"))
    user_mapping_file: str = os.getenv("USER_MAPPING_FILE", "user_mapping.json")
    require_user_mapping: bool = os.getenv("REQUIRE_USER_MAPPING",
                                           "false").lower() == "true"
    mcp_host: str = os.getenv("MCP_HOST", "127.0.0.1")
    mcp_port: int = int(os.getenv("MCP_PORT", "8000"))
    mcp_enabled: bool = os.getenv("MCP_ENABLED", "true").lower() == "true"
    goose_provider: Optional[str] = os.getenv("GOOSE_PROVIDER")
    goose_model: Optional[str] = os.getenv("GOOSE_MODEL")
    goose_openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    goose_anthropic_api_key: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
    goose_google_api_key: Optional[str] = os.getenv("GOOGLE_API_KEY")
    goose_mistral_api_key: Optional[str] = os.getenv("MISTRAL_API_KEY")
    goose_builtin_extensions: List[str] = None
    goose_mcp_servers: List[dict] = None

    def __post_init__(self):
        if self.approved_users is None:
            self.approved_users = [
                u.strip()
                for u in os.getenv("APPROVED_USERS", "").split(",")
                if u.strip()
            ]
        if self.goose_provider is None:
            self.goose_provider = os.getenv("GOOSE_PROVIDER")
        if self.goose_model is None:
            self.goose_model = os.getenv("GOOSE_MODEL")
        if self.goose_openai_api_key is None:
            self.goose_openai_api_key = os.getenv("OPENAI_API_KEY")
        if self.goose_anthropic_api_key is None:
            self.goose_anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        if self.goose_google_api_key is None:
            self.goose_google_api_key = os.getenv("GOOGLE_API_KEY")
        if self.goose_mistral_api_key is None:
            self.goose_mistral_api_key = os.getenv("MISTRAL_API_KEY")
        if self.goose_builtin_extensions is None:
            self.goose_builtin_extensions = [
                e.strip()
                for e in os.getenv("GOOSE_BUILTIN_EXTENSIONS", "").split(",")
                if e.strip()
            ]
        if self.goose_mcp_servers is None:
            mcp_json = os.getenv("GOOSE_MCP_SERVERS", "[]")
            try:
                import json
                self.goose_mcp_servers = json.loads(mcp_json)
            except Exception:
                self.goose_mcp_servers = []


# Default instance
default_config = Config()
