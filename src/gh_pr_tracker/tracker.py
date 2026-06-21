"""Fetch, classify, and diff tracked pull requests."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from gh_pr_tracker.model import (
    DISPLAY_CATEGORIES,
    REPO_NAME_PARTS,
    ROLE_AUTHOR,
    ROLE_COMMENTER,
    ROLE_REVIEWER,
    ROLE_WATCHED,
    CollectConfig,
    DiffState,
    DiscoverConfig,
    FailingCheck,
    PRClassifyInput,
    PRFetchRequest,
    PRSnapshot,
    ReviewAfterMine,
    StoredPR,
    TrackerEvent,
    UnansweredBreakdown,
)

if TYPE_CHECKING:
    from gh_pr_tracker.github import GitHubClient

ProgressCallback = Callable[[str], None]

SIGN_OFF_PREFIXES = ("approved-", "lgtm-")
FAILING_CHECK_CONCLUSIONS = frozenset(
    {
        "failure",
        "timed_out",
        "cancelled",
        "action_required",
        "startup_failure",
        "stale",
    },
)

_CATEGORY_ORDER = {name: index for index, name in enumerate(DISPLAY_CATEGORIES)}


def sort_snapshots(snapshots: list[PRSnapshot]) -> list[PRSnapshot]:
    """Sort by display category priority, then attention, then oldest author activity."""
    return sorted(
        snapshots,
        key=lambda item: (
            _CATEGORY_ORDER.get(item.display_category(), len(_CATEGORY_ORDER)),
            not item.needs_attention,
            item.last_author_activity_at,
        ),
    )


def group_snapshots_by_category(
    snapshots: list[PRSnapshot],
) -> list[tuple[str, list[PRSnapshot]]]:
    """Return non-empty category sections in display order."""
    buckets: dict[str, list[PRSnapshot]] = {name: [] for name in DISPLAY_CATEGORIES}
    for snapshot in sort_snapshots(snapshots):
        buckets[snapshot.display_category()].append(snapshot)
    return [(name, buckets[name]) for name in DISPLAY_CATEGORIES if buckets[name]]


def parse_repo(repo: str) -> tuple[str, str]:
    parts = repo.split("/", maxsplit=1)
    if len(parts) != REPO_NAME_PARTS or not parts[0] or not parts[1]:
        msg = f"Invalid repo format: {repo!r}. Expected owner/repo."
        raise ValueError(msg)
    return parts[0], parts[1]


def mention_pattern(login: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-])@{re.escape(login)}(?![\w-])", re.IGNORECASE)


def _parse_github_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _comment_login(comment: dict[str, Any]) -> str | None:
    author = comment.get("author")
    if isinstance(author, dict):
        login = author.get("login")
        if login:
            return str(login)
    user = comment.get("user")
    if isinstance(user, dict):
        login = user.get("login")
        if login:
            return str(login)
    return None


def _thread_comments(thread: dict[str, Any]) -> list[dict[str, Any]]:
    comments = thread.get("comments") or {}
    nodes = comments.get("nodes") or []
    return sorted(nodes, key=lambda item: _parse_github_datetime(item.get("createdAt")))


def _mentions_user(body: str | None, pattern: re.Pattern[str]) -> bool:
    return bool(body and pattern.search(body))


def _has_reply_from_login(
    comments: list[dict[str, Any]],
    *,
    login: str,
    start_index: int,
) -> bool:
    return any(_comment_login(later) == login for later in comments[start_index + 1 :])


def _record_unanswered_thread(
    breakdown: UnansweredBreakdown,
    *,
    seen_ids: set[str],
    thread_id: str,
    url: str | None,
    field: str,
) -> None:
    if thread_id in seen_ids:
        return
    seen_ids.add(thread_id)
    if field == "started":
        breakdown.threads_started += 1
        if url:
            breakdown.threads_started_urls.append(url)
    else:
        breakdown.threads_joined += 1
        if url:
            breakdown.threads_joined_urls.append(url)


@dataclass(frozen=True)
class _ThreadReplyCheck:
    first_login: str | None
    last_login: str | None
    login: str
    my_comments: list[dict[str, Any]]
    thread_id: str
    thread_url: str | None


def _unanswered_thread_reply(
    context: _ThreadReplyCheck,
    *,
    breakdown: UnansweredBreakdown,
    seen_ids: set[str],
) -> None:
    if context.last_login == context.login:
        return
    if context.first_login == context.login:
        _record_unanswered_thread(
            breakdown,
            seen_ids=seen_ids,
            thread_id=context.thread_id,
            url=context.thread_url,
            field="started",
        )
    elif context.my_comments:
        _record_unanswered_thread(
            breakdown,
            seen_ids=seen_ids,
            thread_id=context.thread_id,
            url=context.thread_url,
            field="joined",
        )


def _process_thread_mentions(
    comments: list[dict[str, Any]],
    *,
    login: str,
    pattern: re.Pattern[str],
    breakdown: UnansweredBreakdown,
    seen_ids: set[str],
) -> None:
    for index, comment in enumerate(comments):
        body = comment.get("body")
        if not _mentions_user(str(body) if body else None, pattern):
            continue
        if _comment_login(comment) == login:
            continue
        if _has_reply_from_login(comments, login=login, start_index=index):
            continue
        comment_id = str(comment.get("id"))
        if comment_id in seen_ids:
            continue
        seen_ids.add(comment_id)
        breakdown.mentions += 1
        url = comment.get("url")
        if url:
            breakdown.mention_urls.append(str(url))


def _process_review_thread(
    thread: dict[str, Any],
    *,
    login: str,
    pattern: re.Pattern[str],
    breakdown: UnansweredBreakdown,
    seen_ids: set[str],
) -> None:
    if thread.get("isResolved"):
        return
    comments = _thread_comments(thread)
    if not comments:
        return

    thread_id = str(thread.get("id") or comments[0].get("id"))
    first_login = _comment_login(comments[0])
    my_comments = [comment for comment in comments if _comment_login(comment) == login]
    last_login = _comment_login(comments[-1])
    thread_url = str(comments[0].get("url")) if comments[0].get("url") else None

    _unanswered_thread_reply(
        _ThreadReplyCheck(
            first_login=first_login,
            last_login=last_login,
            login=login,
            my_comments=my_comments,
            thread_id=thread_id,
            thread_url=thread_url,
        ),
        breakdown=breakdown,
        seen_ids=seen_ids,
    )
    _process_thread_mentions(
        comments,
        login=login,
        pattern=pattern,
        breakdown=breakdown,
        seen_ids=seen_ids,
    )


def _process_issue_mentions(
    issue_comments: list[dict[str, Any]],
    *,
    login: str,
    pattern: re.Pattern[str],
    breakdown: UnansweredBreakdown,
    seen_ids: set[str],
) -> None:
    sorted_comments = sorted(
        issue_comments,
        key=lambda item: _parse_github_datetime(item.get("created_at")),
    )
    for index, comment in enumerate(sorted_comments):
        body = comment.get("body")
        if not _mentions_user(str(body) if body else None, pattern):
            continue
        if _comment_login(comment) == login:
            continue
        if _has_reply_from_login(sorted_comments, login=login, start_index=index):
            continue
        comment_id = str(comment.get("id"))
        if comment_id in seen_ids:
            continue
        seen_ids.add(comment_id)
        breakdown.mentions += 1
        url = comment.get("html_url")
        if url:
            breakdown.mention_urls.append(str(url))


def compute_unanswered(
    *,
    login: str,
    review_threads: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
) -> UnansweredBreakdown:
    pattern = mention_pattern(login)
    breakdown = UnansweredBreakdown()
    seen_ids: set[str] = set()

    for thread in review_threads:
        _process_review_thread(
            thread,
            login=login,
            pattern=pattern,
            breakdown=breakdown,
            seen_ids=seen_ids,
        )

    _process_issue_mentions(
        issue_comments,
        login=login,
        pattern=pattern,
        breakdown=breakdown,
        seen_ids=seen_ids,
    )
    return breakdown


def _review_url(review: dict[str, Any]) -> str | None:
    html_url = review.get("html_url")
    return str(html_url) if html_url else None


def _review_signals(
    *,
    login: str,
    head_sha: str,
    reviews: list[dict[str, Any]],
) -> tuple[bool | None, list[ReviewAfterMine]]:
    submitted = [review for review in reviews if review.get("state") != "PENDING" and _comment_login(review)]
    my_reviews = [review for review in submitted if _comment_login(review) == login and review.get("submitted_at")]
    if not my_reviews:
        return None, []

    my_last = max(my_reviews, key=lambda review: _parse_github_datetime(review.get("submitted_at")))
    my_last_at = _parse_github_datetime(my_last.get("submitted_at"))
    commit_id = my_last.get("commit_id")
    new_commits: bool | None = head_sha != commit_id if commit_id else None

    reviews_after_mine: list[ReviewAfterMine] = []
    for review in submitted:
        reviewer = _comment_login(review)
        if not reviewer or reviewer == login:
            continue
        if _parse_github_datetime(review.get("submitted_at")) <= my_last_at:
            continue
        url = _review_url(review)
        if url is None:
            continue
        reviews_after_mine.append(ReviewAfterMine(reviewer=reviewer, url=url))
    reviews_after_mine.sort(key=lambda item: item.reviewer)
    return new_commits, reviews_after_mine


def _last_author_push_at(
    *,
    author: str,
    commits: list[dict[str, Any]],
) -> datetime | None:
    latest: datetime | None = None
    for commit in commits:
        commit_author = commit.get("author") or {}
        login = commit_author.get("login") if isinstance(commit_author, dict) else None
        if login != author:
            continue
        commit_data = commit.get("commit") or {}
        author_block = commit_data.get("author") or commit_data.get("committer") or {}
        parsed = _parse_github_datetime(str(author_block.get("date") or ""))
        if parsed == datetime.min.replace(tzinfo=UTC):
            continue
        if latest is None or parsed > latest:
            latest = parsed
    return latest


def _max_datetime(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def compute_last_author_activity_at(
    *,
    author: str,
    created_at: datetime,
    commits: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    review_threads: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
) -> datetime:
    issue_comment_times = [
        _parse_github_datetime(str(comment.get("created_at") or ""))
        for comment in issue_comments
        if _comment_login(comment) == author
    ]
    review_times = [
        _parse_github_datetime(str(review.get("submitted_at") or ""))
        for review in reviews
        if review.get("state") != "PENDING"
        and _comment_login(review) == author
        and review.get("submitted_at")
    ]
    thread_comment_times: list[datetime] = []
    for thread in review_threads:
        for comment in _thread_comments(thread):
            if _comment_login(comment) != author:
                continue
            thread_comment_times.append(_parse_github_datetime(str(comment.get("createdAt") or "")))

    latest = _max_datetime(
        _last_author_push_at(author=author, commits=commits),
        _max_datetime(*issue_comment_times) if issue_comment_times else None,
        _max_datetime(*review_times) if review_times else None,
        _max_datetime(*thread_comment_times) if thread_comment_times else None,
    )
    return latest or created_at


def _pull_label_names(pull: dict[str, Any]) -> tuple[str, ...]:
    raw_labels = pull.get("labels") or []
    names = sorted(
        str(label.get("name"))
        for label in raw_labels
        if isinstance(label, dict) and label.get("name")
    )
    return tuple(names)


def extract_sign_off_labels(labels: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(label for label in labels if label.startswith(SIGN_OFF_PREFIXES)))


def compute_failing_checks(check_runs: list[dict[str, Any]]) -> tuple[FailingCheck, ...]:
    failing: list[FailingCheck] = []
    for check_run in check_runs:
        conclusion = check_run.get("conclusion")
        if conclusion not in FAILING_CHECK_CONCLUSIONS:
            continue
        name = check_run.get("name")
        if not name:
            continue
        html_url = check_run.get("html_url")
        failing.append(
            FailingCheck(
                name=str(name),
                url=str(html_url) if html_url else None,
            ),
        )
    failing.sort(key=lambda item: item.name)
    return tuple(failing)


def classify_pr(data: PRClassifyInput) -> PRSnapshot:
    head = data.pull.get("head") or {}
    head_sha = str(head.get("sha") or "")
    user = data.pull.get("user") or {}
    author_login = user.get("login")
    author = str(author_login or "")
    created_at = _parse_github_datetime(str(data.pull.get("created_at") or ""))
    merged_roles = set(data.roles)
    if author_login == data.login:
        merged_roles.add(ROLE_AUTHOR)

    unanswered = compute_unanswered(
        login=data.login,
        review_threads=data.review_threads,
        issue_comments=data.issue_comments,
    )
    new_commits, reviews_after_mine = _review_signals(
        login=data.login,
        head_sha=head_sha,
        reviews=data.reviews,
    )
    labels = _pull_label_names(data.pull)
    return PRSnapshot(
        number=int(data.pull["number"]),
        title=str(data.pull.get("title") or ""),
        url=str(data.pull.get("html_url") or ""),
        author=author,
        roles=merged_roles,
        head_sha=head_sha,
        created_at=created_at,
        last_author_activity_at=compute_last_author_activity_at(
            author=author,
            created_at=created_at,
            commits=data.commits,
            reviews=data.reviews,
            review_threads=data.review_threads,
            issue_comments=data.issue_comments,
        ),
        unanswered=unanswered,
        new_commits_after_review=new_commits,
        others_reviews_after_mine=len(reviews_after_mine),
        reviews_after_mine=reviews_after_mine,
        labels=labels,
        sign_off_labels=extract_sign_off_labels(labels),
        failing_checks=compute_failing_checks(data.check_runs),
        state=str(data.pull.get("state") or "open"),
        merged=bool(data.pull.get("merged")),
    )


def snapshot_to_stored(snapshot: PRSnapshot) -> StoredPR:
    return StoredPR(
        head_sha=snapshot.head_sha,
        unanswered_count=snapshot.unanswered_count,
        new_commits_after_review=snapshot.new_commits_after_review,
        others_reviews_after_mine=snapshot.others_reviews_after_mine,
        title=snapshot.title,
        url=snapshot.url,
        author=snapshot.author,
        threads_started=snapshot.unanswered.threads_started,
        threads_joined=snapshot.unanswered.threads_joined,
        mentions=snapshot.unanswered.mentions,
    )


def stored_to_snapshot(number: int, stored: StoredPR, roles: set[str] | None = None) -> PRSnapshot:
    now = datetime.now(tz=UTC)
    return PRSnapshot(
        number=number,
        title=stored.title,
        url=stored.url,
        author=stored.author,
        roles=roles or set(),
        head_sha=stored.head_sha,
        created_at=now,
        last_author_activity_at=now,
        unanswered=UnansweredBreakdown(
            threads_started=stored.threads_started,
            threads_joined=stored.threads_joined,
            mentions=stored.mentions,
        ),
        new_commits_after_review=stored.new_commits_after_review,
        others_reviews_after_mine=stored.others_reviews_after_mine,
    )


async def discover_pr_numbers(
    client: GitHubClient,
    config: DiscoverConfig,
    on_progress: ProgressCallback | None = None,
) -> dict[int, set[str]]:
    role_map: dict[int, set[str]] = {}

    async def add_from_search(qualifier: str, role: str) -> None:
        if on_progress:
            on_progress(f"Searching {role} PRs…")
        items = await client.search_open_prs(config.repo, qualifier)
        for item in items:
            number = int(item["number"])
            role_map.setdefault(number, set()).add(role)

    await add_from_search(f"reviewed-by:{config.login}", ROLE_REVIEWER)
    await add_from_search(f"commenter:{config.login}", ROLE_COMMENTER)
    await add_from_search(f"author:{config.login}", ROLE_AUTHOR)
    for number in config.watched:
        role_map.setdefault(number, set()).add(ROLE_WATCHED)
    for label in config.watched_labels:
        qualifier = f'label:"{label}"' if " " in label else f"label:{label}"
        if on_progress:
            on_progress(f"Searching label {label!r}…")
        items = await client.search_open_prs(config.repo, qualifier)
        for item in items:
            number = int(item["number"])
            role_map.setdefault(number, set()).add(ROLE_WATCHED)
    return role_map


async def _fetch_pr_payload(client: GitHubClient, request: PRFetchRequest) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    pull = await client.get_pr_details(request.owner, request.repo, request.number)
    head = pull.get("head") or {}
    head_sha = str(head.get("sha") or "")
    reviews, commits, threads, issue_comments, check_runs = await asyncio.gather(
        client.get_pr_reviews(request.owner, request.repo, request.number),
        client.get_pull_commits(request.owner, request.repo, request.number),
        client.get_pr_review_threads(request.owner, request.repo, request.number),
        client.get_issue_comments(request.owner, request.repo, request.number),
        client.get_commit_check_runs(request.owner, request.repo, head_sha),
    )
    return pull, reviews, commits, threads, issue_comments, check_runs


async def fetch_pr_snapshot(client: GitHubClient, request: PRFetchRequest) -> PRSnapshot | None:
    try:
        pull, reviews, commits, threads, issue_comments, check_runs = await _fetch_pr_payload(client, request)
    except ValueError:
        return None

    if pull.get("state") != "open":
        return None

    return classify_pr(
        PRClassifyInput(
            login=request.login,
            pull=pull,
            reviews=reviews,
            commits=commits,
            review_threads=threads,
            issue_comments=issue_comments,
            check_runs=check_runs,
            roles=request.roles,
        ),
    )


async def fetch_pr_detail_any_state(client: GitHubClient, request: PRFetchRequest) -> PRSnapshot | None:
    try:
        pull, reviews, commits, threads, issue_comments, check_runs = await _fetch_pr_payload(client, request)
    except ValueError:
        return None

    return classify_pr(
        PRClassifyInput(
            login=request.login,
            pull=pull,
            reviews=reviews,
            commits=commits,
            review_threads=threads,
            issue_comments=issue_comments,
            check_runs=check_runs,
            roles=request.roles,
        ),
    )


async def collect_snapshots(
    client: GitHubClient,
    config: CollectConfig,
    on_progress: ProgressCallback | None = None,
) -> list[PRSnapshot]:
    role_map = await discover_pr_numbers(
        client,
        DiscoverConfig(
            repo=config.repo,
            login=config.login,
            watched=config.watched,
            watched_labels=config.watched_labels,
        ),
        on_progress=on_progress,
    )
    owner, repo_name = parse_repo(config.repo)
    numbers = sorted(role_map)
    snapshots: list[PRSnapshot] = []
    semaphore = asyncio.Semaphore(5)

    async def fetch_one(index: int, number: int) -> None:
        async with semaphore:
            if on_progress:
                on_progress(f"Fetching PR #{number} ({index}/{len(numbers)})…")
            snapshot = await fetch_pr_snapshot(
                client,
                PRFetchRequest(
                    owner=owner,
                    repo=repo_name,
                    number=number,
                    login=config.login,
                    roles=role_map[number],
                ),
            )
            if snapshot is not None:
                snapshots.append(snapshot)

    await asyncio.gather(*(fetch_one(index, number) for index, number in enumerate(numbers, start=1)))
    return sort_snapshots(snapshots)


def diff_snapshots(
    *,
    previous: DiffState | None,
    current: list[PRSnapshot],
) -> list[TrackerEvent]:
    now = datetime.now(tz=UTC)
    events: list[TrackerEvent] = []
    current_map = {snapshot.number: snapshot for snapshot in current}
    previous_map = previous.prs if previous else {}

    for number, snapshot in current_map.items():
        old = previous_map.get(number)
        if old is None:
            events.append(
                TrackerEvent(
                    type="pr_opened",
                    pr_number=number,
                    title=snapshot.title,
                    url=snapshot.url,
                    details={},
                    at=now,
                ),
            )
            continue
        if old.head_sha != snapshot.head_sha:
            events.append(
                TrackerEvent(
                    type="new_commits",
                    pr_number=number,
                    title=snapshot.title,
                    url=snapshot.url,
                    details={"old_sha": old.head_sha, "new_sha": snapshot.head_sha},
                    at=now,
                ),
            )
        if snapshot.others_reviews_after_mine > old.others_reviews_after_mine:
            events.append(
                TrackerEvent(
                    type="new_review",
                    pr_number=number,
                    title=snapshot.title,
                    url=snapshot.url,
                    details={
                        "old_count": old.others_reviews_after_mine,
                        "new_count": snapshot.others_reviews_after_mine,
                    },
                    at=now,
                ),
            )
        if snapshot.unanswered_count > old.unanswered_count:
            events.append(
                TrackerEvent(
                    type="unanswered_reply",
                    pr_number=number,
                    title=snapshot.title,
                    url=snapshot.url,
                    details={
                        "old_count": old.unanswered_count,
                        "new_count": snapshot.unanswered_count,
                    },
                    at=now,
                ),
            )
        elif snapshot.unanswered_count < old.unanswered_count:
            events.append(
                TrackerEvent(
                    type="unanswered_cleared",
                    pr_number=number,
                    title=snapshot.title,
                    url=snapshot.url,
                    details={
                        "old_count": old.unanswered_count,
                        "new_count": snapshot.unanswered_count,
                    },
                    at=now,
                ),
            )

    for number, old in previous_map.items():
        if number not in current_map:
            events.append(
                TrackerEvent(
                    type="pr_closed",
                    pr_number=number,
                    title=old.title,
                    url=old.url,
                    details={"merged": None},
                    at=now,
                ),
            )

    return events
