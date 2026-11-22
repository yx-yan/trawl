"""Settings from the environment, and which boards to collect from (sources.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REPO_SOURCES = REPO / "sources.toml"
HOME = Path.home() / ".trawl"  # personal files, never in the repo
HOME_CRITERIA = HOME / "criteria.toml"


@dataclass
class Settings:
    database_url: str = "postgresql://trawl:trawl@localhost:5432/trawl"
    sources_path: Path = REPO_SOURCES
    criteria_path: Path = HOME_CRITERIA
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    # the résumé's class and fonts, to compile tailored résumés (deploy/deploy.sh profile)
    resume_class: Path = HOME / "resume-class"
    bank_path: Path = HOME / "bank.toml"  # spare bullets the tailor may use
    profile_path: Path = HOME / "profile.enc"  # encrypted; the key is TRAWL_PROFILE_KEY in .env
    tailor_min_score: int = 60  # tailor a résumé for jobs that fit at least this well
    tailor_limit: int = 10  # at most this many per run

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.environ.get("TRAWL_DATABASE_URL", cls.database_url),
            sources_path=Path(os.environ.get("TRAWL_SOURCES", REPO_SOURCES)),
            criteria_path=Path(os.environ.get("TRAWL_CRITERIA", HOME_CRITERIA)),
            telegram_token=os.environ.get("TRAWL_TELEGRAM_TOKEN"),
            telegram_chat_id=os.environ.get("TRAWL_TELEGRAM_CHAT_ID"),
            resume_class=Path(os.environ.get("TRAWL_RESUME_CLASS", cls.resume_class)),
            bank_path=Path(os.environ.get("TRAWL_BANK", cls.bank_path)),
            profile_path=Path(os.environ.get("TRAWL_PROFILE", cls.profile_path)),
            tailor_min_score=int(os.environ.get("TRAWL_TAILOR_MIN_SCORE", cls.tailor_min_score)),
            tailor_limit=int(os.environ.get("TRAWL_TAILOR_LIMIT", cls.tailor_limit)),
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

    def wants(self, location: str) -> bool:
        loc = location.lower()
        return any(w.lower() in loc for w in self.locations)


def load_dotenv(path: Path = Path(".env")) -> None:
    """KEY=value lines into os.environ, without overriding what's already set."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
