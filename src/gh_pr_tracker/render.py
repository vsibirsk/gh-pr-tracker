"""Rich and JSON rendering."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self

from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TaskID, TextColumn
from rich.table import Table

from gh_pr_tracker.tracker import group_snapshots_by_category

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from gh_pr_tracker.model import PRSnapshot, ReviewAfterMine, TrackerEvent

console = Console()

_CELL_LINE = "\n"

STATUS_TABLE_COLUMNS: Sequence[tuple[str, dict[str, Any]]] = (
    ("#", {"style": "cyan", "width": 5, "no_wrap": True}),
    ("Title", {"ratio": 4, "min_width": 50}),
    ("Owner", {"min_width": 14, "no_wrap": True, "overflow": "ignore"}),
    ("Roles", {"min_width": 12, "overflow": "ignore"}),
    ("Age", {"width": 6, "no_wrap": True}),
    ("Idle", {"width": 6, "no_wrap": True}),
    ("Sign-off", {"min_width": 14, "overflow": "ignore"}),
    ("CI", {"ratio": 2, "min_width": 18, "overflow": "ignore"}),
    ("Pending", {"ratio": 2, "min_width": 20, "overflow": "ignore"}),
)


def human_age(value: datetime) -> str:
    now = datetime.now(tz=UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    if value == datetime.min.replace(tzinfo=UTC):
        return "-"
    delta = now - value.astimezone(UTC)
    days = delta.days
    if days > 0:
        return f"{days}d"
    hours = delta.seconds // 3600
    if hours > 0:
        return f"{hours}h"
    minutes = max(delta.seconds // 60, 1)
    return f"{minutes}m"


def format_roles(roles: set[str]) -> str:
    order = ["author", "reviewer", "commenter", "watched"]
    return _CELL_LINE.join(role for role in order if role in roles)


def _rich_link(label: str, url: str) -> str:
    return f"[link={url}]{label}[/link]"


def _styled_link(label: str, url: str, *, style: str) -> str:
    return f"[{style}][link={url}]{label}[/link][/{style}]"


def _grouped_thread_link(label: str, urls: list[str], *, style: str = "") -> str | None:
    if not urls:
        return None
    text = f"{label}({len(urls)})"
    if style:
        return _styled_link(text, urls[0], style=style)
    return _rich_link(text, urls[0])


def _review_links(reviews: list[ReviewAfterMine]) -> list[str]:
    return [
        _styled_link(f"reviews(1): {review.reviewer}", review.url, style="magenta")
        for review in reviews
    ]


def format_pending_flags(snapshot: PRSnapshot) -> str:
    parts: list[str] = []
    for label, urls, style in (
        ("started", snapshot.unanswered.threads_started_urls, ""),
        ("joined", snapshot.unanswered.threads_joined_urls, ""),
        ("mention", snapshot.unanswered.mention_urls, "yellow"),
    ):
        link = _grouped_thread_link(label, urls, style=style)
        if link:
            parts.append(link)
    if snapshot.new_commits_after_review:
        parts.append(_rich_link("new-commits", f"{snapshot.url}/commits/{snapshot.head_sha}"))
    parts.extend(_review_links(snapshot.reviews_after_mine))
    return _CELL_LINE.join(parts)


def format_sign_off_labels(snapshot: PRSnapshot) -> str:
    if not snapshot.sign_off_labels:
        return "-"
    return _CELL_LINE.join(f"[green]{label}[/green]" for label in snapshot.sign_off_labels)


def format_ci_checks(snapshot: PRSnapshot) -> str:
    if not snapshot.failing_checks:
        return "[green]all pass[/green]"
    return _CELL_LINE.join(f"[red]{check.name}[/red]" for check in snapshot.failing_checks)


def _build_status_table(*, title: str) -> Table:
    table = Table(title=title, expand=True, show_lines=True)
    for name, kwargs in STATUS_TABLE_COLUMNS:
        table.add_column(name, **kwargs)
    return table


def render_status_table(snapshots: list[PRSnapshot]) -> None:
    sections = group_snapshots_by_category(snapshots)
    if not sections:
        console.print("No open pull requests to show.")
        return

    for index, (category, items) in enumerate(sections):
        if index:
            console.print()
        table = _build_status_table(title=category.capitalize())
        for snapshot in items:
            pending = format_pending_flags(snapshot)
            table.add_row(
                _rich_link(str(snapshot.number), snapshot.url),
                snapshot.title,
                snapshot.author or "-",
                format_roles(snapshot.roles),
                human_age(snapshot.created_at),
                human_age(snapshot.last_author_activity_at),
                format_sign_off_labels(snapshot),
                format_ci_checks(snapshot),
                pending or "-",
            )
        console.print(table)


def render_profile_header(profile_key: str) -> None:
    console.print(f"[bold]{profile_key}[/bold]")


def render_diff(events: list[TrackerEvent]) -> None:
    if not events:
        console.print("[green]No changes since last run.[/green]")
        return
    for event in events:
        console.print(f"[bold]{event.type}[/bold] #{event.pr_number} {event.title} — {event.url}")
        if event.details:
            console.print(f"  {event.details}")


def render_pr_detail(snapshot: PRSnapshot) -> None:
    sign_off = ", ".join(snapshot.sign_off_labels) if snapshot.sign_off_labels else "-"
    ci = ", ".join(check.name for check in snapshot.failing_checks) if snapshot.failing_checks else "all pass"
    lines = [
        f"PR #{snapshot.number}: {snapshot.title}",
        f"State: {snapshot.state}" + (" (merged)" if snapshot.merged else ""),
        f"Owner: {snapshot.author or '-'}",
        f"Roles: {format_roles(snapshot.roles) or '-'}",
        f"Opened: {human_age(snapshot.created_at)} ago · Author idle: {human_age(snapshot.last_author_activity_at)}",
        f"Head SHA: {snapshot.head_sha}",
        f"Sign-off labels: {sign_off}",
        f"CI: {ci}",
        "",
        "Unanswered breakdown:",
        f"  started threads: {snapshot.unanswered.threads_started}",
        f"  joined threads: {snapshot.unanswered.threads_joined}",
        f"  mentions: {snapshot.unanswered.mentions}",
        "",
        f"New commits after my review: {snapshot.new_commits_after_review}",
        f"Others reviewed after me: {snapshot.others_reviews_after_mine}",
        f"URL: {snapshot.url}",
    ]
    url_lines: list[str] = []
    for label, urls in (
        ("Started threads", snapshot.unanswered.threads_started_urls),
        ("Joined threads", snapshot.unanswered.threads_joined_urls),
        ("Mentions", snapshot.unanswered.mention_urls),
    ):
        if urls:
            url_lines.append(f"{label}:")
            url_lines.extend(f"  - {url}" for url in urls)
    if snapshot.reviews_after_mine:
        url_lines.append("Reviews after mine:")
        url_lines.extend(f"  - {review.reviewer}: {review.url}" for review in snapshot.reviews_after_mine)
    if snapshot.new_commits_after_review:
        url_lines.append("New commits:")
        url_lines.append(f"  - {snapshot.url}/commits/{snapshot.head_sha}")
    if url_lines:
        lines.extend(["", *url_lines])
    console.print(Panel(Group(*lines)))


def snapshot_to_json(snapshot: PRSnapshot) -> dict[str, Any]:
    return {
        "number": snapshot.number,
        "title": snapshot.title,
        "url": snapshot.url,
        "author": snapshot.author,
        "roles": sorted(snapshot.roles),
        "head_sha": snapshot.head_sha,
        "created_at": snapshot.created_at.astimezone(UTC).isoformat(),
        "last_author_activity_at": snapshot.last_author_activity_at.astimezone(UTC).isoformat(),
        "labels": list(snapshot.labels),
        "sign_off_labels": list(snapshot.sign_off_labels),
        "failing_checks": [
            {"name": check.name, "url": check.url} for check in snapshot.failing_checks
        ],
        "unanswered_count": snapshot.unanswered_count,
        "unanswered": {
            "started_threads": {
                "count": snapshot.unanswered.threads_started,
                "urls": snapshot.unanswered.threads_started_urls,
            },
            "joined_threads": {
                "count": snapshot.unanswered.threads_joined,
                "urls": snapshot.unanswered.threads_joined_urls,
            },
            "mentions": {
                "count": snapshot.unanswered.mentions,
                "urls": snapshot.unanswered.mention_urls,
            },
        },
        "pending": {
            "new_commits": {
                "active": snapshot.new_commits_after_review is True,
                "url": f"{snapshot.url}/commits/{snapshot.head_sha}" if snapshot.new_commits_after_review else None,
            },
            "new_reviews": [
                {"reviewer": review.reviewer, "url": review.url} for review in snapshot.reviews_after_mine
            ],
        },
        "new_commits_after_review": snapshot.new_commits_after_review,
        "others_reviews_after_mine": snapshot.others_reviews_after_mine,
        "needs_attention": snapshot.needs_attention,
        "display_category": snapshot.display_category(),
        "state": snapshot.state,
        "merged": snapshot.merged,
    }


def event_to_json(event: TrackerEvent) -> dict[str, Any]:
    return {
        "type": event.type,
        "pr_number": event.pr_number,
        "title": event.title,
        "url": event.url,
        "details": event.details,
        "at": event.at.astimezone(UTC).isoformat(),
    }


def print_json(data: object) -> None:
    pass


class FetchProgress:
    """Rich progress wrapper for fetch operations."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._progress: Progress | None = None
        self._task: TaskID | None = None

    def __enter__(self) -> Self:
        if self.enabled:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True,
            )
            self._progress.__enter__()
            self._task = self._progress.add_task("Starting…", total=None)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._progress is not None:
            self._progress.__exit__(exc_type, exc_val, exc_tb)

    def update(self, message: str) -> None:
        if self._progress is not None and self._task is not None:
            self._progress.update(self._task, description=message)
