"""Tests for rendering helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from gh_pr_tracker.model import PRSnapshot, ReviewAfterMine, UnansweredBreakdown
from gh_pr_tracker.render import format_attention_flags, snapshot_to_json


def test_format_attention_flags_links() -> None:
    snapshot = PRSnapshot(
        number=1,
        title="T",
        url="https://github.com/org/repo/pull/1",
        author="other",
        roles=set(),
        head_sha="deadbeef",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        last_push_at=datetime(2026, 6, 17, tzinfo=UTC),
        unanswered=UnansweredBreakdown(
            threads_started=1,
            mentions=1,
            threads_started_urls=["https://github.com/org/repo/pull/1#discussion_r1"],
            mention_urls=["https://github.com/org/repo/pull/1#issuecomment-1"],
        ),
        new_commits_after_review=True,
        others_reviews_after_mine=1,
        reviews_after_mine=[
            ReviewAfterMine(
                reviewer="jdoe",
                url="https://github.com/org/repo/pull/1#pullrequestreview-9",
            ),
        ],
    )
    flags = format_attention_flags(snapshot)
    assert "started threads(1)" in flags
    assert "mention(1)" in flags
    assert "new-commits" in flags
    assert "new-reviews(jdoe)" in flags
    assert "discussion_r1" in flags
    assert "pullrequestreview-9" in flags


def test_snapshot_to_json_flags() -> None:
    snapshot = PRSnapshot(
        number=1,
        title="T",
        url="https://github.com/org/repo/pull/1",
        author="other",
        roles=set(),
        head_sha="deadbeef",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        last_push_at=datetime(2026, 6, 17, tzinfo=UTC),
        unanswered=UnansweredBreakdown(),
        new_commits_after_review=False,
        others_reviews_after_mine=0,
    )
    payload = snapshot_to_json(snapshot)
    assert payload["author"] == "other"
    assert "created_at" in payload
    assert payload["flags"]["new_commits"]["active"] is False
