"""Profile loading. One process is bound to one wwApi for its whole life."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".config" / "wwmcp" / "config.json"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Profile:
    name: str
    base_url: str
    api_key: str


def load_profile(env: dict[str, str] | None = None) -> Profile:
    """Resolve the profile from WWMCP_CONFIG / WWMCP_PROFILE (else the file's
    default_profile). No silent fallback: a missing or unknown profile raises."""
    env = os.environ if env is None else env
    path = Path(env.get("WWMCP_CONFIG") or DEFAULT_CONFIG)
    try:
        cfg = json.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"no config at {path} (see config.example.json)") from None
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}") from None

    if not isinstance(cfg, dict) or not isinstance(cfg.get("profiles") or {}, dict):
        raise ConfigError(f"{path} must be an object with a \"profiles\" object")
    profiles = cfg.get("profiles") or {}
    name = env.get("WWMCP_PROFILE") or cfg.get("default_profile")
    if not name:
        raise ConfigError(
            "no profile selected: set WWMCP_PROFILE or default_profile in " + str(path)
        )
    if name not in profiles:
        raise ConfigError(
            f"profile '{name}' not in {path} (have: {', '.join(sorted(profiles)) or 'none'})"
        )
    p = profiles[name]
    if not isinstance(p, dict):
        raise ConfigError(f"profile '{name}' in {path} must be an object")
    base_url = str(p.get("base_url") or "").rstrip("/")
    api_key = str(p.get("api_key") or "")
    if not base_url or not api_key:
        raise ConfigError(f"profile '{name}' needs both base_url and api_key")
    return Profile(name=name, base_url=base_url, api_key=api_key)
