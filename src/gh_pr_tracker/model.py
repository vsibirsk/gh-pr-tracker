"""GitHub PR tracker models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime
    from pathlib import Path

ROLE_AUTHOR = "author"
ROLE_REVIEWER = "reviewer"
ROLE_COMMENTER = "commenter"
ROLE_WATCHED = "watched"

CATEGORY_MENTIONED = "mentioned"
CATEGORY_AUTHORED = "authored"
CATEGORY_REVIEWED = "reviewed"
CATEGORY_COMMENTED = "commented"
CATEGORY_WATCHED = "watched"

DISPLAY_CATEGORIES = (
    CATEGORY_MENTIONED,
    CATEGORY_AUTHORED,
    CATEGORY_REVIEWED,
    CATEGORY_COMMENTED,
    CATEGORY_WATCHED,
)

DEFAULT_REPO = "RedHatQE/openshift-virtualization-tests"
CONFIG_VERSION = 1
STATE_VERSION = 1
REPO_NAME_PARTS = 2


@dataclass(frozen=True)
class FailingCheck:
    """A failing CI check on the PR head commit."""

    name: str
    url: str | None


@dataclass
class PRClassifyInput:
    """Raw GitHub API payloads for PR classification."""

    login: str
    pull: dict[str, Any]
    reviews: list[dict[str, Any]]
    commits: list[dict[str, Any]]
    review_threads: list[dict[str, Any]]
    issue_comments: list[dict[str, Any]]
    check_runs: list[dict[str, Any]]
    roles: set[str]


@dataclass
class PRFetchRequest:
    """Target repository and PR identity for fetching."""

    owner: str
    repo: str
    number: int
    login: str
    roles: set[str]


@dataclass
class StatusRunConfig:
    """Options for the default status command."""

    diff: bool
    repo: str
    user: str | None
    watch: list[int]
    unwatch: list[int]
    watch_labels: list[str]
    unwatch_labels: list[str]
    json_output: bool
    state_file: Path | None
    no_state: bool
    all_profiles: bool


@dataclass
class WatchChangeConfig:
    """Options for watch add/remove commands."""

    repo: str
    user: str | None
    add: list[int]
    remove: list[int]


@dataclass
class WatchLabelChangeConfig:
    """Options for watch label add/remove commands."""

    repo: str
    user: str | None
    add: list[str]
    remove: list[str]


@dataclass(frozen=True)
class DiscoverConfig:
    """Inputs for discovering tracked pull request numbers."""

    repo: str
    login: str
    watched: Iterable[int]
    watched_labels: Iterable[str]


@dataclass(frozen=True)
class CollectConfig:
    """Inputs for collecting PR snapshots."""

    repo: str
    login: str
    watched: list[int]
    watched_labels: list[str]


@dataclass(frozen=True)
class ReviewAfterMine:
    """A review submitted after the user's last review."""

    reviewer: str
    url: str


@dataclass
class UnansweredBreakdown:
    """Counts of unanswered items by category."""

    threads_started: int = 0
    threads_joined: int = 0
    mentions: int = 0
    threads_started_urls: list[str] = field(default_factory=list)
    threads_joined_urls: list[str] = field(default_factory=list)
    mention_urls: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.threads_started + self.threads_joined + self.mentions


@dataclass
class PRSnapshot:
    """Snapshot of a tracked pull request."""

    number: int
    title: str
    url: str
    author: str
    roles: set[str]
    head_sha: str
    created_at: datetime
    last_author_activity_at: datetime
    unanswered: UnansweredBreakdown
    new_commits_after_review: bool | None
    others_reviews_after_mine: int
    reviews_after_mine: list[ReviewAfterMine] = field(default_factory=list)
    labels: tuple[str, ...] = ()
    sign_off_labels: tuple[str, ...] = ()
    failing_checks: tuple[FailingCheck, ...] = ()
    state: str = "open"
    merged: bool = False

    @property
    def unanswered_count(self) -> int:
        return self.unanswered.total

    @property
    def needs_attention(self) -> bool:
        return self.unanswered_count > 0 or self.new_commits_after_review is True or self.others_reviews_after_mine > 0

    def display_category(self) -> str:
        """Primary output bucket by user-facing priority."""
        if self.unanswered.mentions > 0:
            return CATEGORY_MENTIONED
        if ROLE_AUTHOR in self.roles:
            return CATEGORY_AUTHORED
        if ROLE_REVIEWER in self.roles:
            return CATEGORY_REVIEWED
        if ROLE_COMMENTER in self.roles:
            return CATEGORY_COMMENTED
        return CATEGORY_WATCHED


@dataclass
class TrackerEvent:
    """Change detected between runs."""

    type: str
    pr_number: int
    title: str
    url: str
    details: dict[str, Any]
    at: datetime


@dataclass
class StoredPR:
    """PR fields persisted in state."""

    head_sha: str
    unanswered_count: int
    new_commits_after_review: bool | None
    others_reviews_after_mine: int
    title: str
    url: str
    author: str = ""
    threads_started: int = 0
    threads_joined: int = 0
    mentions: int = 0


@dataclass
class DiffState:
    """Diff baseline persisted per profile."""

    version: int
    updated_at: datetime
    prs: dict[int, StoredPR]
