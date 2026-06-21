"""Pytest fixtures."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from gh_pr_tracker.model import ROLE_REVIEWER, PRClassifyInput


@pytest.fixture
def login() -> str:
    return "vsibirsk"


@dataclass
class MockClassifyParts:
    pull: dict
    reviews: list[dict]
    commits: list[dict]
    threads: list[dict]
    issue_comments: list[dict]


@pytest.fixture
def mock_classify_parts(login: str) -> MockClassifyParts:
    return MockClassifyParts(
        pull={
            "number": 123,
            "title": "Test PR",
            "html_url": "https://github.com/org/repo/pull/123",
            "state": "open",
            "merged": False,
            "created_at": "2026-06-01T10:00:00Z",
            "head": {"sha": "abc123"},
            "user": {"login": "other"},
        },
        reviews=[
            {
                "user": {"login": login},
                "state": "COMMENTED",
                "submitted_at": "2026-06-10T10:00:00Z",
                "commit_id": "abc123",
                "html_url": "https://github.com/org/repo/pull/123#pullrequestreview-1",
            },
            {
                "user": {"login": "reviewer2"},
                "state": "APPROVED",
                "submitted_at": "2026-06-11T10:00:00Z",
                "commit_id": "abc123",
                "html_url": "https://github.com/org/repo/pull/123#pullrequestreview-2",
            },
        ],
        commits=[
            {
                "author": {"login": "other"},
                "commit": {
                    "author": {"date": "2026-06-12T10:00:00Z"},
                    "committer": {"date": "2026-06-12T10:00:00Z"},
                },
            },
        ],
        threads=[
            {
                "id": "thread-1",
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "id": "c1",
                            "url": "https://github.com/org/repo/pull/123#discussion_r1",
                            "body": "Please fix",
                            "createdAt": "2026-06-10T09:00:00Z",
                            "author": {"login": login},
                        },
                        {
                            "id": "c2",
                            "url": "https://github.com/org/repo/pull/123#discussion_r2",
                            "body": "Updated",
                            "createdAt": "2026-06-10T11:00:00Z",
                            "author": {"login": "other"},
                        },
                    ],
                },
            },
        ],
        issue_comments=[
            {
                "id": 99,
                "html_url": "https://github.com/org/repo/pull/123#issuecomment-1",
                "body": f"Hey @{login} please look",
                "created_at": "2026-06-10T08:00:00Z",
                "user": {"login": "other"},
            },
        ],
    )


@pytest.fixture
def classify_input(login: str, mock_classify_parts: MockClassifyParts) -> PRClassifyInput:
    parts = mock_classify_parts
    return PRClassifyInput(
        login=login,
        pull=parts.pull,
        reviews=parts.reviews,
        commits=parts.commits,
        review_threads=parts.threads,
        issue_comments=parts.issue_comments,
        check_runs=[],
        roles=set(),
    )


@pytest.fixture
def reviewer_classify_input(classify_input: PRClassifyInput) -> PRClassifyInput:
    return replace(classify_input, roles={ROLE_REVIEWER})
