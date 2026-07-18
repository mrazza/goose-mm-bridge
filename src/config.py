from dataclasses import dataclass
import os
from typing import List, Optional

from dotenv import load_dotenv

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(project_root, ".env"))


@dataclass
class Config:
    mattermost_url: str = os.getenv("MATTERMOST_URL", "").strip().rstrip('/')
    mattermost_token: str = os.getenv("MATTERMOST_TOKEN")
    mattermost_scheme: str = os.getenv("MATTERMOST_SCHEME", "https")
    mattermost_port: str = os.getenv("MATTERMOST_PORT", "443")
    approved_users: List[str] = None
    admin_users: List[str] = None
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
    user_configs_dir: str = os.getenv("USER_CONFIGS_DIR", "user_configs")
    user_env_vars: Optional[dict] = None
    agents_config_file: str = os.getenv("AGENTS_CONFIG_FILE",
                                        "agents_config.json")
    default_agent: str = os.getenv("DEFAULT_AGENT", "goose")
    user_agent_preferences_file: str = os.getenv("USER_AGENT_PREFERENCES_FILE",
                                                 "user_agent_preferences.json")
    agents: dict = None

    def __post_init__(self):
        if self.approved_users is None:
            self.approved_users = [
                u.strip()
                for u in os.getenv("APPROVED_USERS", "").split(",")
                if u.strip()
            ]
        if self.admin_users is None:
            self.admin_users = [
                u.strip()
                for u in os.getenv("ADMIN_USERS", "").split(",")
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

        # Load agents config
        agents_config_path = self.agents_config_file
        if not os.path.isabs(agents_config_path):
            agents_config_path = os.path.join(project_root, agents_config_path)

        self.agents = {}
        if os.path.exists(agents_config_path):
            try:
                import json
                with open(agents_config_path, 'r') as f:
                    self.agents = json.load(f)
            except Exception as e:
                print(
                    f"Error loading agents config from {agents_config_path}: {e}"
                )


# Default instance
default_config = Config()


def load_user_config(linux_user: str, base_config: Config) -> Config:
    """Loads user-specific config overrides from user_configs/{linux_user}.env if it exists."""
    import dataclasses
    from dotenv import dotenv_values

    user_configs_dir = base_config.user_configs_dir
    if not os.path.isabs(user_configs_dir):
        user_configs_dir = os.path.join(project_root, user_configs_dir)

    config_path = os.path.join(user_configs_dir, f"{linux_user}.env")
    if not os.path.exists(config_path):
        return base_config

    try:
        user_env = dotenv_values(config_path)
    except Exception as e:
        print(
            f"[{datetime.now() if 'datetime' in globals() else os.getpid()}] Error loading user config from {config_path}: {e}"
        )
        return base_config

    if not user_env:
        return base_config

    overrides = {}

    # Helper function to convert env strings to boolean
    def parse_bool(v: str) -> bool:
        return v.lower() == "true"

    # Helper to parse list of strings
    def parse_list(v: str) -> List[str]:
        return [x.strip() for x in v.split(",") if x.strip()]

    # Helper to parse JSON
    def parse_json(v: str):
        try:
            import json
            return json.loads(v)
        except Exception:
            return []

    # Map of environment variable name -> (config_field_name, conversion_fn)
    mapping = {
        "MATTERMOST_URL": ("mattermost_url", lambda v: v.strip().rstrip('/')),
        "MATTERMOST_TOKEN": ("mattermost_token", str),
        "MATTERMOST_SCHEME": ("mattermost_scheme", str),
        "MATTERMOST_PORT": ("mattermost_port", str),
        "APPROVED_USERS": ("approved_users", parse_list),
        "ADMIN_USERS": ("admin_users", parse_list),
        "POLL_INTERVAL": ("poll_interval", int),
        "DEBUG": ("debug", parse_bool),
        "GOOSE_THINKING_TRACE": ("goose_thinking_trace", parse_bool),
        "GOOSE_THINKING_TRACE_SIMPLIFIED":
            ("goose_thinking_trace_simplified", parse_bool),
        "RPC_TIMEOUT": ("rpc_timeout", int),
        "MAX_SESSIONS": ("max_sessions", int),
        "USER_MAPPING_FILE": ("user_mapping_file", str),
        "REQUIRE_USER_MAPPING": ("require_user_mapping", parse_bool),
        "MCP_HOST": ("mcp_host", str),
        "MCP_PORT": ("mcp_port", int),
        "MCP_ENABLED": ("mcp_enabled", parse_bool),
        "GOOSE_PROVIDER": ("goose_provider", str),
        "GOOSE_MODEL": ("goose_model", str),
        "OPENAI_API_KEY": ("goose_openai_api_key", str),
        "ANTHROPIC_API_KEY": ("goose_anthropic_api_key", str),
        "GOOGLE_API_KEY": ("goose_google_api_key", str),
        "MISTRAL_API_KEY": ("goose_mistral_api_key", str),
        "GOOSE_BUILTIN_EXTENSIONS": ("goose_builtin_extensions", parse_list),
        "GOOSE_MCP_SERVERS": ("goose_mcp_servers", parse_json),
        "AGENTS_CONFIG_FILE": ("agents_config_file", str),
        "DEFAULT_AGENT": ("default_agent", str),
        "USER_AGENT_PREFERENCES_FILE": ("user_agent_preferences_file", str),
    }

    for env_key, val in user_env.items():
        if val is None:
            continue
        if env_key in mapping:
            field_name, convert_fn = mapping[env_key]
            try:
                overrides[field_name] = convert_fn(val)
            except Exception as e:
                print(f"Error parsing user config option {env_key}: {e}")

    overrides["user_env_vars"] = dict(user_env)
    new_config = dataclasses.replace(base_config, **overrides)

    agents_config_path = new_config.agents_config_file
    if not os.path.isabs(agents_config_path):
        agents_config_path = os.path.join(project_root, agents_config_path)

    new_config.agents = {}
    if os.path.exists(agents_config_path):
        try:
            import json
            with open(agents_config_path, 'r') as f:
                new_config.agents = json.load(f)
        except Exception as e:
            print(f"Error loading agents config from {agents_config_path}: {e}")
    return new_config
