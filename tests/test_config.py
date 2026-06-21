"""Tests for config persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from gh_pr_tracker.config import (
    ProfileSettings,
    TrackerConfig,
    apply_watch_prs_changes,
    ensure_profile,
    load_config,
    parse_profile_key,
    parse_state_filename,
    profile_key,
    profile_state_path,
    remove_profile,
    save_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_profile_key_and_paths() -> None:
    key = profile_key(repo="RedHatQE/openshift-virtualization-tests", user="vsibirsk")
    assert key == "RedHatQE/openshift-virtualization-tests@vsibirsk"
    path = profile_state_path(repo="RedHatQE/openshift-virtualization-tests", user="vsibirsk")
    assert path.name == "RedHatQE+openshift-virtualization-tests@vsibirsk.json"


def test_parse_profile_key() -> None:
    repo, user = parse_profile_key("org/repo@alice")
    assert repo == "org/repo"
    assert user == "alice"


def test_parse_state_filename() -> None:
    repo, user = parse_state_filename(
        profile_state_path(repo="org/repo", user="alice"),
    )
    assert repo == "org/repo"
    assert user == "alice"


def test_apply_watch_prs_changes() -> None:
    assert apply_watch_prs_changes([1, 2], add=[3], remove=[1]) == [2, 3]


def test_save_and_load_config(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("gh_pr_tracker.config.CONFIG_PATH", config_path)
    config = TrackerConfig(
        version=1,
        profiles={
            "org/repo@me": ProfileSettings(
                watched_prs=[10],
                watched_labels=["needs-qe"],
            ),
        },
    )
    save_config(config)
    loaded = load_config()
    assert loaded.profiles["org/repo@me"].watched_prs == [10]
    assert loaded.profiles["org/repo@me"].watched_labels == ["needs-qe"]


def test_remove_profile() -> None:
    config = TrackerConfig(version=1, profiles={})
    ensure_profile(config, repo="org/repo", user="me")
    assert remove_profile(config, repo="org/repo", user="me") is True
    assert remove_profile(config, repo="org/repo", user="me") is False


def test_parse_profile_key_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid profile key"):
        parse_profile_key("no-at-sign")
