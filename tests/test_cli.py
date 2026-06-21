"""Tests for CLI."""

from __future__ import annotations

import pytest

from gh_pr_tracker.cli import build_parser, main
from gh_pr_tracker.config import ProfileSettings, TrackerConfig, load_config, profile_state_path, save_config


def test_help() -> None:
    help_text = build_parser().format_help()
    assert "Track GitHub PRs" in help_text
    assert "--diff" in help_text
    assert "--all" in help_text


def test_default_status_flags() -> None:
    args = build_parser().parse_args(["--diff", "--watch-pr", "1,2"])
    assert args.command is None
    assert args.diff is True
    assert args.watch == "1,2"


def test_watch_list_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("gh_pr_tracker.config.CONFIG_PATH", tmp_path / "config.json")
    main(["--user", "me", "--repo", "org/repo", "watch", "list"])


def test_state_clean(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("gh_pr_tracker.config.STATE_DIR", tmp_path / "state")
    state_path = profile_state_path(repo="org/repo", user="me")
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{}", encoding="utf-8")
    main(["--user", "me", "--repo", "org/repo", "state", "clean"])
    assert not state_path.exists()


def test_state_clean_all(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("gh_pr_tracker.config.STATE_DIR", tmp_path / "state")
    root = tmp_path / "state"
    root.mkdir()
    (root / "a+r@u.json").write_text("{}", encoding="utf-8")
    main(["state", "clean", "--all"])
    assert not root.exists()


def test_config_clean(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("gh_pr_tracker.config.CONFIG_PATH", config_path)
    save_config(
        TrackerConfig(
            version=1,
            profiles={"org/repo@me": ProfileSettings(watched_prs=[1])},
        ),
    )
    main(["--user", "me", "--repo", "org/repo", "config", "clean"])
    assert load_config().profiles == {}


def test_profile_list(tmp_path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("gh_pr_tracker.config.CONFIG_PATH", config_path)
    monkeypatch.setattr("gh_pr_tracker.config.STATE_DIR", tmp_path / "state")
    save_config(
        TrackerConfig(
            version=1,
            profiles={"org/repo@me": ProfileSettings(watched_prs=[1], watched_labels=["l"])},
        ),
    )
    main(["profile", "list"])
    output = capsys.readouterr().out
    assert "org/repo@me" in output
    assert "labels=l" in output


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
