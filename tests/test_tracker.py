"""Tests for tracker classification."""

from __future__ import annotations

from datetime import UTC, datetime

from gh_pr_tracker.model import (
    CATEGORY_AUTHORED,
    CATEGORY_COMMENTED,
    CATEGORY_MENTIONED,
    CATEGORY_REVIEWED,
    ROLE_AUTHOR,
    ROLE_COMMENTER,
    ROLE_REVIEWER,
    DiffState,
    PRClassifyInput,
    PRSnapshot,
    StoredPR,
    UnansweredBreakdown,
)
from gh_pr_tracker.tracker import (
    classify_pr,
    compute_failing_checks,
    compute_last_author_activity_at,
    compute_unanswered,
    diff_snapshots,
    extract_sign_off_labels,
    group_snapshots_by_category,
    parse_repo,
    snapshot_to_stored,
    sort_snapshots,
)

EXPECTED_UNANSWERED_COUNT = 2  # one started thread + one @mention


def test_parse_repo() -> None:
    assert parse_repo("RedHatQE/openshift-virtualization-tests") == (
        "RedHatQE",
        "openshift-virtualization-tests",
    )


def test_compute_unanswered(login: str, mock_classify_parts) -> None:
    breakdown = compute_unanswered(
        login=login,
        review_threads=mock_classify_parts.threads,
        issue_comments=mock_classify_parts.issue_comments,
    )
    assert breakdown.threads_started == 1
    assert breakdown.mentions == 1


def test_classify_pr(reviewer_classify_input: PRClassifyInput) -> None:
    snapshot = classify_pr(reviewer_classify_input)
    assert snapshot.unanswered_count == EXPECTED_UNANSWERED_COUNT
    assert snapshot.author == "other"
    assert snapshot.new_commits_after_review is False
    assert snapshot.others_reviews_after_mine == 1
    assert len(snapshot.reviews_after_mine) == 1
    assert snapshot.reviews_after_mine[0].reviewer == "reviewer2"
    assert snapshot.needs_attention is True
    assert snapshot.display_category() == CATEGORY_MENTIONED


def test_display_category_priority() -> None:
    now = datetime.now(tz=UTC)
    mentioned = PRSnapshot(
        number=1,
        title="M",
        url="https://example/1",
        author="alice",
        roles={ROLE_REVIEWER},
        head_sha="a",
        created_at=now,
        last_author_activity_at=now,
        unanswered=UnansweredBreakdown(mentions=1),
        new_commits_after_review=False,
        others_reviews_after_mine=0,
    )
    authored = PRSnapshot(
        number=2,
        title="A",
        url="https://example/2",
        author="bob",
        roles={ROLE_AUTHOR},
        head_sha="b",
        created_at=now,
        last_author_activity_at=now,
        unanswered=UnansweredBreakdown(),
        new_commits_after_review=False,
        others_reviews_after_mine=0,
    )
    reviewed = PRSnapshot(
        number=3,
        title="R",
        url="https://example/3",
        author="carol",
        roles={ROLE_REVIEWER},
        head_sha="c",
        created_at=now,
        last_author_activity_at=now,
        unanswered=UnansweredBreakdown(),
        new_commits_after_review=False,
        others_reviews_after_mine=0,
    )
    commented = PRSnapshot(
        number=4,
        title="C",
        url="https://example/4",
        author="dave",
        roles={ROLE_COMMENTER},
        head_sha="d",
        created_at=now,
        last_author_activity_at=now,
        unanswered=UnansweredBreakdown(),
        new_commits_after_review=False,
        others_reviews_after_mine=0,
    )
    ordered = sort_snapshots([commented, reviewed, authored, mentioned])
    assert [item.number for item in ordered] == [1, 2, 3, 4]

    sections = group_snapshots_by_category([commented, reviewed, authored, mentioned])
    assert [name for name, _ in sections] == [
        CATEGORY_MENTIONED,
        CATEGORY_AUTHORED,
        CATEGORY_REVIEWED,
        CATEGORY_COMMENTED,
    ]


def test_diff_snapshots() -> None:
    previous = DiffState(
        version=1,
        updated_at=datetime.now(tz=UTC),
        prs={
            1: StoredPR(
                head_sha="old",
                unanswered_count=0,
                new_commits_after_review=False,
                others_reviews_after_mine=0,
                title="One",
                url="https://example/1",
            ),
        },
    )
    current = [
        PRSnapshot(
            number=2,
            title="Two",
            url="https://example/2",
            author="eve",
            roles=set(),
            head_sha="sha",
            created_at=datetime.now(tz=UTC),
            last_author_activity_at=datetime.now(tz=UTC),
            unanswered=UnansweredBreakdown(),
            new_commits_after_review=False,
            others_reviews_after_mine=0,
        ),
    ]
    events = diff_snapshots(previous=previous, current=current)
    types = {event.type for event in events}
    assert "pr_opened" in types
    assert "pr_closed" in types


def test_compute_last_author_activity_at(login: str, mock_classify_parts) -> None:
    parts = mock_classify_parts
    created_at = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    latest = compute_last_author_activity_at(
        author="other",
        created_at=created_at,
        commits=parts.commits,
        reviews=parts.reviews,
        review_threads=parts.threads,
        issue_comments=parts.issue_comments,
    )
    assert latest == datetime(2026, 6, 12, 10, 0, tzinfo=UTC)

    with_comment = compute_last_author_activity_at(
        author="other",
        created_at=created_at,
        commits=[],
        reviews=[],
        review_threads=[],
        issue_comments=[
            {
                "id": 1,
                "created_at": "2026-06-20T10:00:00Z",
                "user": {"login": "other"},
            },
        ],
    )
    assert with_comment == datetime(2026, 6, 20, 10, 0, tzinfo=UTC)


def test_snapshot_to_stored(classify_input: PRClassifyInput) -> None:
    snapshot = classify_pr(classify_input)
    stored = snapshot_to_stored(snapshot)
    assert stored.unanswered_count == snapshot.unanswered_count
    assert stored.author == "other"


def test_extract_sign_off_labels() -> None:
    assert extract_sign_off_labels(["bug", "approved-qe", "lgtm-bot"]) == ("approved-qe", "lgtm-bot")
    assert extract_sign_off_labels(["bug"]) == ()


def test_compute_failing_checks() -> None:
    checks = compute_failing_checks(
        [
            {"name": "ok", "conclusion": "success"},
            {"name": "broken", "conclusion": "failure", "html_url": "https://example/check"},
            {"name": "running", "conclusion": None, "status": "in_progress"},
        ],
    )
    assert len(checks) == 1
    assert checks[0].name == "broken"
    assert checks[0].url == "https://example/check"


def test_classify_pr_labels_and_checks(classify_input: PRClassifyInput) -> None:
    data = classify_input
    pull = {**data.pull, "labels": [{"name": "approved-qe"}, {"name": "bug"}]}
    snapshot = classify_pr(
        PRClassifyInput(
            login=data.login,
            pull=pull,
            reviews=data.reviews,
            commits=data.commits,
            review_threads=data.review_threads,
            issue_comments=data.issue_comments,
            check_runs=[{"name": "unit", "conclusion": "failure"}],
            roles=data.roles,
        ),
    )
    assert snapshot.labels == ("approved-qe", "bug")
    assert snapshot.sign_off_labels == ("approved-qe",)
    assert snapshot.failing_checks[0].name == "unit"
