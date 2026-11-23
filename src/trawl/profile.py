"""The candidate's personal answers: contact details, work authorization, dates, EEO choices.

They fill application forms, but never reach the model: it only says which profile field a
question asks for (it sees the question, not the answer), and code puts the value in. The file
is encrypted (Fernet) with a key kept apart from it: the key lives in the trawl checkout's `.env`
(TRAWL_PROFILE_KEY), the file in `~/.trawl/profile.enc`, so copying one without the other reveals
nothing. `trawl profile edit` decrypts it into an editor and encrypts it again.
"""

from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field

PROFILE_PATH = Path.home() / ".trawl" / "profile.enc"
KEY_ENV = "TRAWL_PROFILE_KEY"


class Profile(BaseModel):
    """Every field is optional: a question asking for a missing one waits for the person."""

    legal_first_name: str | None = None
    legal_last_name: str | None = None
    preferred_name: str | None = Field(None, description="Full name as you like to be called")
    name_pronunciation: str | None = None
    pronouns: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin: str | None = None
    github: str | None = None
    website: str | None = None
    city: str | None = None
    country: str | None = Field(None, description="Country of residence")
    university: str | None = None
    degree: str | None = None
    major: str | None = None
    graduation: str | None = Field(None, description="YYYY-MM")
    previous_university: str | None = Field(None, description="An earlier degree, e.g. your bachelor's")
    previous_degree: str | None = None
    previous_major: str | None = None
    previous_graduation: str | None = Field(None, description="YYYY-MM")
    gpa: str | None = None
    start_date: str | None = Field(None, description="Earliest full-time start, YYYY-MM")
    authorized_to_work: bool | None = Field(None, description="Already allowed to work in the job's country")
    needs_sponsorship: bool | None = Field(None, description="Needs a work pass sponsored, now or later")
    current_employer: str | None = None
    current_title: str | None = None
    how_heard: str | None = Field(None, description="e.g. Company website")
    salary_expectation: str | None = None
    notice_period: str | None = None
    offer_deadline: str | None = Field(None, description="Any offer or process deadline; 'No' if none")
    # Consents trawl may give for you, by kind; anything else waits for you. You still look over
    # every form before it's submitted, and the audit log names each consent trawl gave.
    agree_privacy: bool | None = Field(None, description="Tick privacy and data-processing acknowledgements")
    agree_truthful: bool | None = Field(None, description="Tick 'the information I give is true'")
    agree_recording: bool | None = Field(None, description="Agree to AI notetakers recording interviews")
    gender: str | None = Field(None, description="Empty: decline to self-identify")
    race: str | None = None
    veteran: str | None = None
    disability: str | None = None


ProfileKey = Literal[tuple(Profile.model_fields) + ("full_name", "resume", "cover_letter")]  # type: ignore[misc]
EEO = ("gender", "race", "veteran", "disability")


def value(profile: Profile, key: str) -> str | bool | None:
    """A profile field, plus full_name made from the legal names."""
    if key == "full_name":
        names = [profile.legal_first_name, profile.legal_last_name]
        return " ".join(n for n in names if n) or None
    return getattr(profile, key, None)


def _fernet(key: str | None = None) -> Fernet:
    key = key or os.environ.get(KEY_ENV)
    if not key:
        raise RuntimeError(f"{KEY_ENV} is not set: run `trawl profile init` and add the key to .env")
    return Fernet(key.encode())


def new_key() -> str:
    return Fernet.generate_key().decode()


def load(path: Path = PROFILE_PATH, key: str | None = None) -> Profile | None:
    """The decrypted profile, or None if there's no file yet."""
    if not path.is_file():
        return None
    try:
        text = _fernet(key).decrypt(path.read_bytes()).decode()
    except InvalidToken as e:
        raise RuntimeError(f"{path} doesn't open with {KEY_ENV}: wrong key?") from e
    return Profile.model_validate(tomllib.loads(text))


def save_text(text: str, path: Path = PROFILE_PATH, key: str | None = None) -> Profile:
    """Check the TOML is a valid profile, then encrypt it to `path` (owner-only)."""
    profile = Profile.model_validate(tomllib.loads(text))
    token = _fernet(key).encrypt(text.encode())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.touch(mode=0o600)
    tmp.write_bytes(token)
    tmp.replace(path)
    return profile


def template() -> str:
    """A TOML skeleton with every field commented out, for a first edit."""
    lines = ["# trawl profile: fills application forms; never sent to the model.",
             "# Uncomment what you want filled; a question asking for a missing field waits for you.", ""]
    for name, f in Profile.model_fields.items():
        hint = f"  # {f.description}" if f.description else ""
        example = "false" if f.annotation == (bool | None) else '""'
        lines.append(f"# {name} = {example}{hint}")
    return "\n".join(lines) + "\n"


def edit(path: Path = PROFILE_PATH, editor: str | None = None) -> Profile:
    """Decrypt into a private temporary file, open the editor, encrypt again; the plain text is
    deleted afterwards. An invalid profile reopens the editor with the error on top."""
    import subprocess

    editor = editor or os.environ.get("EDITOR", "vi")
    current = path.read_bytes() if path.is_file() else None
    text = _fernet().decrypt(current).decode() if current else template()
    with tempfile.TemporaryDirectory() as tmp:
        plain = Path(tmp) / "profile.toml"
        plain.touch(mode=0o600)
        plain.write_text(text)
        while True:
            subprocess.run([*editor.split(), str(plain)], check=True)
            edited = plain.read_text()
            try:
                return save_text(_strip_error(edited), path)
            except Exception as e:  # noqa: BLE001 - show any parse or validation error and retry
                plain.write_text(f"# ERROR: {str(e).splitlines()[0]}\n{_strip_error(edited)}")


def _strip_error(text: str) -> str:
    return "".join(line for line in text.splitlines(keepends=True) if not line.startswith("# ERROR:"))
