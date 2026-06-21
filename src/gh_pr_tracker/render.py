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
    from types import TracebackType

    from gh_pr_tracker.model import PRSnapshot, TrackerEvent

console = Console()


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
    return ",".join(role for role in order if role in roles)


def _rich_link(label: str, url: str) -> str:
    return f"[link={url}]{label}[/link]"


def _thread_flag_links(label: str, urls: list[str]) -> list[str]:
    return [_rich_link(f"{label}(1)", url) for url in urls]


def format_attention_flags(snapshot: PRSnapshot) -> str:
    parts: list[str] = []
    parts.extend(_thread_flag_links("started threads", snapshot.unanswered.threads_started_urls))
    parts.extend(_thread_flag_links("joined threads", snapshot.unanswered.threads_joined_urls))
    parts.extend(_thread_flag_links("mention", snapshot.unanswered.mention_urls))
    if snapshot.new_commits_after_review:
        parts.append(_rich_link("new-commits", f"{snapshot.url}/commits/{snapshot.head_sha}"))
    parts.extend(
        _rich_link(f"new-reviews({review.reviewer})", review.url) for review in snapshot.reviews_after_mine
    )
    return "  ".join(parts)


def render_status_table(snapshots: list[PRSnapshot]) -> None:
    sections = group_snapshots_by_category(snapshots)
    if not sections:
        console.print("No open pull requests to show.")
        return

    for index, (category, items) in enumerate(sections):
        if index:
            console.print()
        table = Table(title=category.capitalize())
        table.add_column("#", style="cyan", no_wrap=True)
        table.add_column("Title", max_width=40)
        table.add_column("Owner", no_wrap=True)
        table.add_column("Roles")
        table.add_column("Age", no_wrap=True)
        table.add_column("Push", no_wrap=True)
        table.add_column("Unans.", no_wrap=True)
        table.add_column("Flags")

        for snapshot in items:
            flags = format_attention_flags(snapshot)
            table.add_row(
                _rich_link(str(snapshot.number), snapshot.url),
                snapshot.title,
                snapshot.author or "-",
                format_roles(snapshot.roles),
                human_age(snapshot.created_at),
                human_age(snapshot.last_push_at),
                str(snapshot.unanswered_count),
                flags or "-",
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
    lines = [
        f"PR #{snapshot.number}: {snapshot.title}",
        f"State: {snapshot.state}" + (" (merged)" if snapshot.merged else ""),
        f"Owner: {snapshot.author or '-'}",
        f"Roles: {format_roles(snapshot.roles) or '-'}",
        f"Opened: {human_age(snapshot.created_at)} ago · Last push: {human_age(snapshot.last_push_at)} ago",
        f"Head SHA: {snapshot.head_sha}",
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
        "last_push_at": snapshot.last_push_at.astimezone(UTC).isoformat(),
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
        "flags": {
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
