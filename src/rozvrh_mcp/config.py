"""Settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from dotenv import load_dotenv

from . import __version__
from .errors import ConfigError

DEFAULT_USER_AGENT = f"rozvrh-mcp/{__version__} (+https://github.com/Dajda2371/RozvrhOptimizer)"


@dataclass(frozen=True)
class Settings:
    base_url: str
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    cache_ttl: int = 600
    timeout: float = 20.0
    max_concurrency: int = 4
    user_agent: str = DEFAULT_USER_AGENT

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    def __repr__(self) -> str:  # never leak the password
        pw = "***" if self.password else None
        return (
            f"Settings(base_url={self.base_url!r}, username={self.username!r}, password={pw}, "
            f"cache_ttl={self.cache_ttl}, timeout={self.timeout}, max_concurrency={self.max_concurrency})"
        )


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{key} must be positive, got {value}")
    return value


def load_settings(env: Mapping[str, str] | None = None, *, dotenv: bool = True) -> Settings:
    """Build Settings from `env` (defaults to os.environ, after loading .env)."""
    if env is None:
        if dotenv:
            load_dotenv(override=False)
        env = os.environ

    base_url = env.get("BAKALARI_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        raise ConfigError("BAKALARI_BASE_URL is required (e.g. https://bakalari.example.cz)")
    if not base_url.startswith(("http://", "https://")):
        raise ConfigError(f"BAKALARI_BASE_URL must start with http:// or https://, got {base_url!r}")

    username = env.get("BAKALARI_USERNAME", "").strip() or None
    password = env.get("BAKALARI_PASSWORD", "") or None
    if bool(username) != bool(password):
        raise ConfigError("BAKALARI_USERNAME and BAKALARI_PASSWORD must be set together")

    timeout_raw = env.get("BAKALARI_TIMEOUT", "").strip()
    try:
        timeout = float(timeout_raw) if timeout_raw else 20.0
    except ValueError as exc:
        raise ConfigError(f"BAKALARI_TIMEOUT must be a number, got {timeout_raw!r}") from exc

    return Settings(
        base_url=base_url,
        username=username,
        password=password,
        cache_ttl=_int(env, "BAKALARI_CACHE_TTL", 600),
        timeout=timeout,
        max_concurrency=_int(env, "BAKALARI_MAX_CONCURRENCY", 4),
        user_agent=env.get("BAKALARI_USER_AGENT", "").strip() or DEFAULT_USER_AGENT,
    )
