"""Tracker configuration (profiles, watches)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from gh_pr_tracker.model import CONFIG_VERSION
from gh_pr_tracker.tracker import parse_repo

CACHE_DIR = Path.home() / ".cache" / "gh-pr-tracker"
CONFIG_PATH = CACHE_DIR / "config.json"
STATE_DIR = CACHE_DIR / "state"


@dataclass
class ProfileSettings:
    """Per-profile tracking preferences."""

    watched_prs: list[int] = field(default_factory=list)
    watched_labels: list[str] = field(default_factory=list)


@dataclass
class TrackerConfig:
    """On-disk tracker configuration."""

    version: int
    profiles: dict[str, ProfileSettings]


def cache_dir() -> Path:
    return CACHE_DIR


def config_path() -> Path:
    return CONFIG_PATH


def state_dir() -> Path:
    return STATE_DIR


def profile_key(*, repo: str, user: str) -> str:
    owner, name = parse_repo(repo)
    return f"{owner}/{name}@{user}"


def parse_profile_key(key: str) -> tuple[str, str]:
    if "@" not in key:
        msg = f"Invalid profile key: {key!r}"
        raise ValueError(msg)
    repo, user = key.rsplit("@", 1)
    parse_repo(repo)
    return repo, user


def profile_state_path(*, repo: str, user: str) -> Path:
    owner, name = parse_repo(repo)
    return state_dir() / f"{owner}+{name}@{user}.json"


def parse_state_filename(path: Path) -> tuple[str, str]:
    stem = path.stem
    if "@" not in stem or "+" not in stem:
        msg = f"Not a profile state file: {path.name}"
        raise ValueError(msg)
    scope, user = stem.rsplit("@", 1)
    owner, name = scope.split("+", 1)
    return f"{owner}/{name}", user


def load_config() -> TrackerConfig:
    if not CONFIG_PATH.exists():
        return TrackerConfig(version=CONFIG_VERSION, profiles={})
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    profiles: dict[str, ProfileSettings] = {}
    for key, value in raw.get("profiles", {}).items():
        profiles[str(key)] = ProfileSettings(
            watched_prs=sorted({int(number) for number in value.get("watched_prs", [])}),
            watched_labels=sorted({str(label) for label in value.get("watched_labels", [])}),
        )
    return TrackerConfig(version=int(raw.get("version", CONFIG_VERSION)), profiles=profiles)


def save_config(config: TrackerConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": config.version,
        "profiles": {
            key: {
                "watched_prs": settings.watched_prs,
                "watched_labels": settings.watched_labels,
            }
            for key, settings in sorted(config.profiles.items())
        },
    }
    CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def list_profile_keys(config: TrackerConfig | None = None) -> list[str]:
    data = config or load_config()
    return sorted(data.profiles)


def ensure_profile(config: TrackerConfig, *, repo: str, user: str) -> ProfileSettings:
    key = profile_key(repo=repo, user=user)
    if key not in config.profiles:
        config.profiles[key] = ProfileSettings()
    return config.profiles[key]


def remove_profile(config: TrackerConfig, *, repo: str, user: str) -> bool:
    key = profile_key(repo=repo, user=user)
    if key not in config.profiles:
        return False
    del config.profiles[key]
    return True


def clean_all_profiles(config: TrackerConfig) -> bool:
    if not config.profiles:
        return False
    config.profiles.clear()
    return True


def apply_watch_prs_changes(
    watched_prs: list[int],
    *,
    add: list[int] | None = None,
    remove: list[int] | None = None,
) -> list[int]:
    current = set(watched_prs)
    if add:
        current.update(add)
    if remove:
        current.difference_update(remove)
    return sorted(current)


def apply_watch_label_changes(
    watched_labels: list[str],
    *,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> list[str]:
    current = set(watched_labels)
    if add:
        current.update(add)
    if remove:
        current.difference_update(remove)
    return sorted(current)
