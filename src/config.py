import dataclasses
from dataclasses import dataclass
from dataclasses import field
import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from dotenv import dotenv_values
from dotenv import load_dotenv

from logging_config import get_logger

logger = get_logger(__name__)

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(project_root, ".env"))


def _parse_bool(value: str) -> bool:
    return value.strip().lower() == "true"


def _parse_list(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_optional_list(value: str) -> Optional[List[str]]:
    items = _parse_list(value)
    return items if items else None


def _parse_json_list(value: str) -> List[dict]:
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:  # pylint: disable=broad-exception-caught
        return []


def _parse_url(value: str) -> str:
    return value.strip().rstrip("/")


@dataclass(frozen=True)
class EnvFieldSpec:
    """Declarative specification for an environment-backed Config field."""

    field_name: str
    env_vars: Tuple[str, ...]
    parser: Callable[[str], Any] = str
    default: Any = None
    default_factory: Optional[Callable[[], Any]] = None
    resolve_when_none: bool = False

    def default_value(self) -> Any:
        if self.default_factory is not None:
            return self.default_factory()
        return self.default

    def resolve_from_env(self) -> Any:
        for env_var in self.env_vars:
            raw = os.getenv(env_var)
            if raw is not None and raw != "":
                return self.parser(raw)
        return self.default_value()


ENV_SPECS: Tuple[EnvFieldSpec, ...] = (
    EnvFieldSpec("mattermost_url", ("MATTERMOST_URL",), _parse_url, ""),
    EnvFieldSpec("mattermost_token", ("MATTERMOST_TOKEN",), str, None),
    EnvFieldSpec("mattermost_scheme", ("MATTERMOST_SCHEME",), str, "https"),
    EnvFieldSpec("mattermost_port", ("MATTERMOST_PORT",), str, "443"),
    EnvFieldSpec("mattermost_ssl_verify", ("MATTERMOST_SSL_VERIFY",),
                 _parse_bool, False),
    EnvFieldSpec("mattermost_http_timeout", ("MATTERMOST_HTTP_TIMEOUT",), int,
                 30),
    EnvFieldSpec("approved_users", ("APPROVED_USERS",),
                 _parse_list,
                 default_factory=list,
                 resolve_when_none=True),
    EnvFieldSpec("admin_users", ("ADMIN_USERS",),
                 _parse_list,
                 default_factory=list,
                 resolve_when_none=True),
    EnvFieldSpec("poll_interval", ("POLL_INTERVAL",), int, 1),
    EnvFieldSpec("debug", ("DEBUG",), _parse_bool, False),
    EnvFieldSpec("goose_thinking_trace",
                 ("THINKING_TRACE", "GOOSE_THINKING_TRACE"), _parse_bool, True),
    EnvFieldSpec(
        "goose_thinking_trace_simplified",
        ("THINKING_TRACE_SIMPLIFIED", "GOOSE_THINKING_TRACE_SIMPLIFIED"),
        _parse_bool, True),
    EnvFieldSpec("rpc_timeout", ("RPC_TIMEOUT",), int, 600),
    EnvFieldSpec("max_sessions", ("MAX_SESSIONS",), int, 100),
    EnvFieldSpec("user_mapping_file", ("USER_MAPPING_FILE",), str,
                 "user_mapping.json"),
    EnvFieldSpec("require_user_mapping", ("REQUIRE_USER_MAPPING",), _parse_bool,
                 False),
    EnvFieldSpec("mcp_host", ("MCP_HOST",), str, "127.0.0.1"),
    EnvFieldSpec("mcp_port", ("MCP_PORT",), int, 8000),
    EnvFieldSpec("mcp_enabled", ("MCP_ENABLED",), _parse_bool, True),
    EnvFieldSpec("goose_provider", ("GOOSE_PROVIDER",),
                 str,
                 None,
                 resolve_when_none=True),
    EnvFieldSpec("goose_model", ("GOOSE_MODEL",),
                 str,
                 None,
                 resolve_when_none=True),
    EnvFieldSpec("goose_openai_api_key", ("OPENAI_API_KEY",),
                 str,
                 None,
                 resolve_when_none=True),
    EnvFieldSpec("goose_anthropic_api_key", ("ANTHROPIC_API_KEY",),
                 str,
                 None,
                 resolve_when_none=True),
    EnvFieldSpec("goose_google_api_key", ("GOOGLE_API_KEY",),
                 str,
                 None,
                 resolve_when_none=True),
    EnvFieldSpec("goose_mistral_api_key", ("MISTRAL_API_KEY",),
                 str,
                 None,
                 resolve_when_none=True),
    EnvFieldSpec("hermes_provider", ("HERMES_PROVIDER",),
                 str,
                 None,
                 resolve_when_none=True),
    EnvFieldSpec("hermes_model", ("HERMES_MODEL",),
                 str,
                 None,
                 resolve_when_none=True),
    EnvFieldSpec("hermes_inference_provider", ("HERMES_INFERENCE_PROVIDER",),
                 str,
                 None,
                 resolve_when_none=True),
    EnvFieldSpec("hermes_inference_model", ("HERMES_INFERENCE_MODEL",),
                 str,
                 None,
                 resolve_when_none=True),
    EnvFieldSpec("hermes_yolo_mode", ("HERMES_YOLO_MODE",),
                 str,
                 None,
                 resolve_when_none=True),
    EnvFieldSpec("goose_builtin_extensions", ("GOOSE_BUILTIN_EXTENSIONS",),
                 _parse_list,
                 default_factory=list,
                 resolve_when_none=True),
    EnvFieldSpec("goose_mcp_servers", ("GOOSE_MCP_SERVERS",),
                 _parse_json_list,
                 default_factory=list,
                 resolve_when_none=True),
    EnvFieldSpec("user_configs_dir", ("USER_CONFIGS_DIR",), str,
                 "user_configs"),
    EnvFieldSpec("agents_config_file", ("AGENTS_CONFIG_FILE",), str,
                 "agents_config.json"),
    EnvFieldSpec("default_agent", ("DEFAULT_AGENT",), str, "goose"),
    EnvFieldSpec("user_agent_preferences_file",
                 ("USER_AGENT_PREFERENCES_FILE",), str,
                 "user_agent_preferences.json"),
    EnvFieldSpec("prewarm_config_file", ("PREWARM_CONFIG_FILE",), str,
                 "prewarm_config.json"),
    EnvFieldSpec("prewarm_users", ("PREWARM_USERS",), str, None),
    EnvFieldSpec("allow_model_control", ("ALLOW_MODEL_CONTROL",), _parse_bool,
                 True),
    EnvFieldSpec("model_control_users", ("MODEL_CONTROL_USERS",),
                 _parse_optional_list,
                 None,
                 resolve_when_none=True),
)

_SPEC_BY_FIELD: Dict[str, EnvFieldSpec] = {
    spec.field_name: spec for spec in ENV_SPECS
}
_SPEC_BY_ENV_VAR: Dict[str, EnvFieldSpec] = {
    env_var: spec for spec in ENV_SPECS for env_var in spec.env_vars
}


def _env(field_name: str) -> Callable[[], Any]:
    return _SPEC_BY_FIELD[field_name].resolve_from_env


def _resolve_project_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(project_root, path)


def _load_agents_config(agents_config_file: str) -> Dict[str, Any]:
    agents_config_path = _resolve_project_path(agents_config_file)
    if not os.path.exists(agents_config_path):
        return {}
    try:
        with open(agents_config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error loading agents config from %s: %s",
                     agents_config_path, e)
        return {}


@dataclass
class Config:
    """Bridge configuration populated from environment and user overrides."""

    mattermost_url: str = field(default_factory=_env("mattermost_url"))
    mattermost_token: Optional[str] = field(
        default_factory=_env("mattermost_token"))
    mattermost_scheme: str = field(default_factory=_env("mattermost_scheme"))
    mattermost_port: str = field(default_factory=_env("mattermost_port"))
    mattermost_ssl_verify: bool = field(
        default_factory=_env("mattermost_ssl_verify"))
    mattermost_http_timeout: int = field(
        default_factory=_env("mattermost_http_timeout"))
    approved_users: Optional[List[str]] = field(
        default_factory=_env("approved_users"))
    admin_users: Optional[List[str]] = field(
        default_factory=_env("admin_users"))
    poll_interval: float = field(default_factory=_env("poll_interval"))
    debug: bool = field(default_factory=_env("debug"))
    goose_thinking_trace: bool = field(
        default_factory=_env("goose_thinking_trace"))
    goose_thinking_trace_simplified: bool = field(
        default_factory=_env("goose_thinking_trace_simplified"))
    rpc_timeout: float = field(default_factory=_env("rpc_timeout"))
    max_sessions: int = field(default_factory=_env("max_sessions"))
    user_mapping_file: str = field(default_factory=_env("user_mapping_file"))
    require_user_mapping: bool = field(
        default_factory=_env("require_user_mapping"))
    mcp_host: str = field(default_factory=_env("mcp_host"))
    mcp_port: int = field(default_factory=_env("mcp_port"))
    mcp_enabled: bool = field(default_factory=_env("mcp_enabled"))
    goose_provider: Optional[str] = field(
        default_factory=_env("goose_provider"))
    goose_model: Optional[str] = field(default_factory=_env("goose_model"))
    goose_openai_api_key: Optional[str] = field(
        default_factory=_env("goose_openai_api_key"))
    goose_anthropic_api_key: Optional[str] = field(
        default_factory=_env("goose_anthropic_api_key"))
    goose_google_api_key: Optional[str] = field(
        default_factory=_env("goose_google_api_key"))
    goose_mistral_api_key: Optional[str] = field(
        default_factory=_env("goose_mistral_api_key"))
    hermes_provider: Optional[str] = field(
        default_factory=_env("hermes_provider"))
    hermes_model: Optional[str] = field(default_factory=_env("hermes_model"))
    hermes_inference_provider: Optional[str] = field(
        default_factory=_env("hermes_inference_provider"))
    hermes_inference_model: Optional[str] = field(
        default_factory=_env("hermes_inference_model"))
    hermes_yolo_mode: Optional[str] = field(
        default_factory=_env("hermes_yolo_mode"))
    goose_builtin_extensions: Optional[List[str]] = field(
        default_factory=_env("goose_builtin_extensions"))
    goose_mcp_servers: Optional[List[dict]] = field(
        default_factory=_env("goose_mcp_servers"))
    user_configs_dir: str = field(default_factory=_env("user_configs_dir"))
    user_env_vars: Optional[dict] = None
    agents_config_file: str = field(default_factory=_env("agents_config_file"))
    default_agent: str = field(default_factory=_env("default_agent"))
    user_agent_preferences_file: str = field(
        default_factory=_env("user_agent_preferences_file"))
    prewarm_config_file: str = field(
        default_factory=_env("prewarm_config_file"))
    prewarm_users: Optional[str] = field(default_factory=_env("prewarm_users"))
    allow_model_control: bool = field(
        default_factory=_env("allow_model_control"))
    model_control_users: Optional[List[str]] = field(
        default_factory=_env("model_control_users"))
    agents: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        for spec in ENV_SPECS:
            if spec.resolve_when_none and getattr(self,
                                                  spec.field_name) is None:
                setattr(self, spec.field_name, spec.resolve_from_env())

        if self.agents is None:
            self.agents = _load_agents_config(self.agents_config_file)

    def get_agent_default_model(self, agent_name: str) -> Optional[str]:
        """Returns the configured default model for an agent using metadata."""
        agent_def = self.agents.get(agent_name, {}) if self.agents else {}
        agent_env = agent_def.get("env") or {}
        model_keys = agent_def.get("model_env")
        if not model_keys:
            upper = agent_name.upper()
            model_keys = [
                f"{upper}_MODEL",
                f"{upper}_INFERENCE_MODEL",
                "MODEL",
            ]

        for key in model_keys:
            if self.user_env_vars and self.user_env_vars.get(key):
                return self.user_env_vars[key]
            attr_val = getattr(self, key.lower(), None)
            if attr_val:
                return attr_val
            if agent_env.get(key):
                return agent_env[key]
        return None


# Default instance
default_config = Config()


def apply_user_env(base_config: Config,
                   user_env: Optional[Dict[str, Any]]) -> Config:
    """Applies parsed .env overrides onto a base Config instance."""
    if not user_env:
        return base_config

    overrides: Dict[str, Any] = {}
    for env_key, val in user_env.items():
        if val is None:
            continue
        spec = _SPEC_BY_ENV_VAR.get(env_key)
        if spec is None:
            continue
        try:
            overrides[spec.field_name] = spec.parser(val)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Error parsing user config option %s: %s", env_key,
                           e)

    overrides["user_env_vars"] = dict(user_env)
    if "agents_config_file" in overrides:
        overrides["agents"] = _load_agents_config(
            overrides["agents_config_file"])

    return dataclasses.replace(base_config, **overrides)


def load_user_config(linux_user: str, base_config: Config) -> Config:
    """Loads per-user overrides from user_configs/{linux_user}.env."""
    user_configs_dir = _resolve_project_path(base_config.user_configs_dir)
    config_path = os.path.join(user_configs_dir, f"{linux_user}.env")
    if not os.path.exists(config_path):
        return base_config

    try:
        user_env = dotenv_values(config_path)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error loading user config from %s: %s", config_path, e)
        return base_config

    return apply_user_env(base_config, user_env)
