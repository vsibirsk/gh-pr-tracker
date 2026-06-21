"""Tests for state persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from gh_pr_tracker.model import DiffState, PRClassifyInput, StoredPR
from gh_pr_tracker.state import build_diff_state, clear_all_state, clear_state, load_state, save_state
from gh_pr_tracker.tracker import classify_pr

if TYPE_CHECKING:
    from pathlib import Path


def test_save_and_load_state(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = DiffState(
        version=1,
        updated_at=datetime(2026, 6, 17, tzinfo=UTC),
        prs={
            10: StoredPR(
                head_sha="abc",
                unanswered_count=1,
                new_commits_after_review=True,
                others_reviews_after_mine=0,
                title="T",
                url="https://example/10",
                author="alice",
                threads_started=1,
            ),
        },
    )
    save_state(path, state)
    loaded = load_state(path)
    assert loaded is not None
    assert loaded.prs[10].author == "alice"
    assert loaded.prs[10].threads_started == 1


def test_build_diff_state(classify_input: PRClassifyInput) -> None:
    snapshot = classify_pr(classify_input)
    state = build_diff_state([snapshot])
    assert state.prs[snapshot.number].head_sha == snapshot.head_sha


def test_clear_state(tmp_path: Path) -> None:
    path = tmp_path / "state" / "org+repo@me.json"
    assert clear_state(path) is False

    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    assert clear_state(path) is True
    assert not path.exists()
    assert not path.parent.exists()


def test_clear_all_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("gh_pr_tracker.config.STATE_DIR", tmp_path / "state")
    root = tmp_path / "state"
    root.mkdir()
    (root / "a+r@u.json").write_text("{}", encoding="utf-8")
    (root / "b+r@u.json").write_text("{}", encoding="utf-8")
    removed = clear_all_state()
    assert len(removed) == 2  # noqa: PLR2004
    assert not root.exists()
