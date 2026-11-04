"""Loads and validates config/profile.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("JOBAGENT_DATA_DIR", ROOT / "data"))
CONFIG_DIR = ROOT / "config"
DEFAULT_CONFIG = Path(os.environ.get("JOBAGENT_CONFIG", CONFIG_DIR / "profile.toml"))
EXAMPLE_CONFIG = CONFIG_DIR / "profile.example.toml"
DB_PATH = DATA_DIR / "jobagent.db"

REQUIRED_PERSONAL = ("first_name", "last_name", "email", "phone")


class ConfigError(Exception):
    pass


@dataclass
class Settings:
    raw: dict[str, Any]
    path: Path

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name, {})
        return value if isinstance(value, dict) else {}

    @property
    def personal(self) -> dict[str, Any]:
        return self.section("personal")

    @property
    def preferences(self) -> dict[str, Any]:
        return self.section("preferences")

    @property
    def limits(self) -> dict[str, Any]:
        return self.section("limits")

    @property
    def answers(self) -> dict[str, Any]:
        return self.section("answers")

    @property
    def email(self) -> dict[str, Any]:
        return self.section("email")

    @property
    def cover_letter_template(self) -> str:
        return str(self.section("cover_letter").get("template", ""))

    @property
    def enabled_platforms(self) -> list[str]:
        return list(self.section("platforms").get("enabled", []))

    def platform(self, name: str) -> dict[str, Any]:
        value = self.section("platforms").get(name, {})
        return value if isinstance(value, dict) else {}

    @property
    def review_mode(self) -> bool:
        return bool(self.limits.get("review_mode", True))

    @property
    def resume_path(self) -> Path | None:
        value = self.personal.get("resume_path")
        if not value:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (self.path.parent / path).resolve()
        return path


def load_settings(path: Path | str | None = None) -> Settings:
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.exists():
        raise ConfigError(
            f"Config not found at {path}. Run `python -m jobagent init` and fill it in."
        )
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc

    settings = Settings(raw=raw, path=path)
    missing = [k for k in REQUIRED_PERSONAL if not settings.personal.get(k)]
    if missing:
        raise ConfigError(f"[personal] is missing: {', '.join(missing)}")
    if not settings.preferences.get("roles"):
        raise ConfigError("[preferences] roles must list at least one job title")
    return settings
