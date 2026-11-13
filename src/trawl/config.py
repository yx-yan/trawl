"""Settings from the environment, and which boards to collect from (sources.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_SOURCES = Path(__file__).resolve().parents[2] / "sources.toml"


@dataclass
class Settings:
    database_url: str = "postgresql://trawl:trawl@localhost:5432/trawl"
    sources_path: Path = REPO_SOURCES
    telegram_token: str | None = None
    telegram_chat_id: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.environ.get("TRAWL_DATABASE_URL", cls.database_url),
            sources_path=Path(os.environ.get("TRAWL_SOURCES", REPO_SOURCES)),
            telegram_token=os.environ.get("TRAWL_TELEGRAM_TOKEN"),
            telegram_chat_id=os.environ.get("TRAWL_TELEGRAM_CHAT_ID"),
        )


@dataclass
class Sources:
    locations: list[str]
    boards: dict[str, list[str]] = field(default_factory=dict)  # connector name -> board keys

    @classmethod
    def load(cls, path: Path) -> Sources:
        data = tomllib.loads(path.read_text())
        boards = {name: section["boards"] for name, section in data.items()
                  if isinstance(section, dict) and "boards" in section}
        return cls(locations=data["locations"]["include"], boards=boards)


def load_dotenv(path: Path = Path(".env")) -> None:
    """KEY=value lines into os.environ, without overriding what's already set."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
