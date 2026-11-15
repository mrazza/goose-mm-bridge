import asyncio
import os
import pwd
from typing import Any, Dict, List, Mapping, Optional, Tuple

from logging_config import get_logger

logger = get_logger(__name__)


def build_agent_launch_spec(
    agent_name: str,
    config,
    linux_user: Optional[str] = None,
    base_env: Optional[Mapping[str, str]] = None,
) -> Tuple[List[str], Dict[str, str]]:
    """Builds the subprocess command line and environment for an ACP agent."""
    agent_def = config.agents.get(agent_name)
    if not agent_def:
        raise ValueError(
            f"Agent '{agent_name}' is not configured in agents_config.json")

    cmd = list(agent_def.get("command", []))
    if not cmd:
        raise ValueError(f"No command configured for agent '{agent_name}'")

    extensions_flag = agent_def.get("extensions_flag")
    if extensions_flag is None and "goose" in cmd:
        extensions_flag = "--with-builtin"
    if extensions_flag and getattr(config, "goose_builtin_extensions", None):
        cmd.extend([extensions_flag, ",".join(config.goose_builtin_extensions)])

    env = dict(base_env if base_env is not None else os.environ)

    static_env = agent_def.get("env") or {}
    for k, v in static_env.items():
        if v is not None:
            env[k] = str(v)

    user_env_vars = getattr(config, "user_env_vars", None)
    forward_env_keys = agent_def.get("forward_env") or []
    for key in forward_env_keys:
        val = None
        if user_env_vars and key in user_env_vars:
            val = user_env_vars[key]
        elif hasattr(config, key.lower()):
            val = getattr(config, key.lower())
        elif hasattr(config, f"goose_{key.lower()}"):
            val = getattr(config, f"goose_{key.lower()}")

        if val is not None:
            env[key] = str(val)

    if user_env_vars:
        for k, v in user_env_vars.items():
            if v is not None:
                env[k] = str(v)

    if linux_user:
        try:
            pw = pwd.getpwnam(linux_user)
            home_dir = pw.pw_dir
            keys_to_pass = set(forward_env_keys) | set(static_env.keys())
            if user_env_vars:
                keys_to_pass.update(user_env_vars.keys())

            env_args = []
            for key in sorted(keys_to_pass):
                if key in env:
                    val = env[key]
                    if val is not None and str(val).strip():
                        env_args.append(f"{key}={val}")

            cmd = [
                "sudo",
                "-n",
                "-u",
                linux_user,
                "-D",
                home_dir,
                "/usr/bin/env",
            ] + env_args + cmd
        except KeyError:
            cmd = ["sudo", "-n", "-u", linux_user, "/usr/bin/env"] + cmd

    return cmd, env


async def terminate_process(
    proc: Any,
    timeout: float = 3.0,
    debug: bool = False,
) -> None:
    """Terminates a subprocess gracefully, killing it if wait() times out."""
    if not proc or getattr(proc, "returncode", None) is not None:
        return
    try:
        proc.terminate()
        if hasattr(proc, "wait"):
            try:
                wait_res = proc.wait()
                if asyncio.iscoroutine(wait_res):
                    await asyncio.wait_for(wait_res, timeout=timeout)
            except (asyncio.TimeoutError, TimeoutError, Exception):  # pylint: disable=broad-exception-caught
                if hasattr(proc, "kill"):
                    try:
                        proc.kill()
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
    except Exception as e:  # pylint: disable=broad-exception-caught
        if debug:
            logger.debug("Error terminating client process: %s", e)
