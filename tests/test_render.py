"""Tests for rendering helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from gh_pr_tracker.model import FailingCheck, PRSnapshot, ReviewAfterMine, UnansweredBreakdown
from gh_pr_tracker.render import (
    STATUS_TABLE_COLUMNS,
    _build_status_table,
    format_ci_checks,
    format_pending_flags,
    format_roles,
    format_sign_off_labels,
    snapshot_to_json,
)


def test_status_table_columns_no_truncation() -> None:
    column_names = dict(STATUS_TABLE_COLUMNS)
    for name in ("Owner", "Roles", "Sign-off"):
        assert column_names[name]["overflow"] == "ignore"
        assert "width" not in column_names[name]


def test_status_table_row_lines() -> None:
    table = _build_status_table(title="Mentioned")
    assert table.show_lines is True


def test_format_pending_flags_links() -> None:
    snapshot = PRSnapshot(
        number=1,
        title="T",
        url="https://github.com/org/repo/pull/1",
        author="other",
        roles=set(),
        head_sha="deadbeef",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        last_author_activity_at=datetime(2026, 6, 17, tzinfo=UTC),
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
    pending = format_pending_flags(snapshot)
    assert pending.splitlines() == [
        "[link=https://github.com/org/repo/pull/1#discussion_r1]started(1)[/link]",
        "[yellow][link=https://github.com/org/repo/pull/1#issuecomment-1]mention(1)[/link][/yellow]",
        "[link=https://github.com/org/repo/pull/1/commits/deadbeef]new-commits[/link]",
        "[magenta][link=https://github.com/org/repo/pull/1#pullrequestreview-9]reviews(1): jdoe[/link][/magenta]",
    ]


def test_format_roles_multiline() -> None:
    rendered = format_roles({"author", "reviewer", "watched"})
    assert rendered.splitlines() == ["author", "reviewer", "watched"]


def test_format_sign_off_labels() -> None:
    snapshot = PRSnapshot(
        number=1,
        title="T",
        url="https://github.com/org/repo/pull/1",
        author="other",
        roles=set(),
        head_sha="deadbeef",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        last_author_activity_at=datetime(2026, 6, 17, tzinfo=UTC),
        unanswered=UnansweredBreakdown(),
        new_commits_after_review=False,
        others_reviews_after_mine=0,
        sign_off_labels=("approved-qe", "lgtm-bot"),
    )
    rendered = format_sign_off_labels(snapshot)
    assert rendered.splitlines() == ["[green]approved-qe[/green]", "[green]lgtm-bot[/green]"]


def test_format_ci_checks() -> None:
    passing = PRSnapshot(
        number=1,
        title="T",
        url="https://github.com/org/repo/pull/1",
        author="other",
        roles=set(),
        head_sha="deadbeef",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        last_author_activity_at=datetime(2026, 6, 17, tzinfo=UTC),
        unanswered=UnansweredBreakdown(),
        new_commits_after_review=False,
        others_reviews_after_mine=0,
    )
    assert format_ci_checks(passing) == "[green]all pass[/green]"

    failing = PRSnapshot(
        number=1,
        title="T",
        url="https://github.com/org/repo/pull/1",
        author="other",
        roles=set(),
        head_sha="deadbeef",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        last_author_activity_at=datetime(2026, 6, 17, tzinfo=UTC),
        unanswered=UnansweredBreakdown(),
        new_commits_after_review=False,
        others_reviews_after_mine=0,
        failing_checks=(
            FailingCheck(name="unit-tests", url="https://example/check"),
            FailingCheck(name="lint", url="https://example/lint"),
        ),
    )
    rendered = format_ci_checks(failing)
    assert rendered.splitlines() == ["[red]unit-tests[/red]", "[red]lint[/red]"]


def test_snapshot_to_json_pending() -> None:
    snapshot = PRSnapshot(
        number=1,
        title="T",
        url="https://github.com/org/repo/pull/1",
        author="other",
        roles=set(),
        head_sha="deadbeef",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        last_author_activity_at=datetime(2026, 6, 17, tzinfo=UTC),
        unanswered=UnansweredBreakdown(),
        new_commits_after_review=False,
        others_reviews_after_mine=0,
        sign_off_labels=("approved-qe",),
    )
    payload = snapshot_to_json(snapshot)
    assert payload["author"] == "other"
    assert payload["sign_off_labels"] == ["approved-qe"]
    assert payload["pending"]["new_commits"]["active"] is False
