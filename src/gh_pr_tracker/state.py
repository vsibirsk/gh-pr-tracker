"""Diff baseline state persistence."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from gh_pr_tracker.config import state_dir
from gh_pr_tracker.model import STATE_VERSION, DiffState, PRSnapshot, StoredPR
from gh_pr_tracker.tracker import snapshot_to_stored

if TYPE_CHECKING:
    from pathlib import Path


def load_state(path: Path) -> DiffState | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    prs: dict[int, StoredPR] = {}
    for key, value in raw.get("prs", {}).items():
        prs[int(key)] = StoredPR(
            head_sha=value["head_sha"],
            unanswered_count=value["unanswered_count"],
            new_commits_after_review=value.get("new_commits_after_review"),
            others_reviews_after_mine=value["others_reviews_after_mine"],
            title=value["title"],
            url=value["url"],
            author=str(value.get("author", "")),
            threads_started=value.get("threads_started", 0),
            threads_joined=value.get("threads_joined", 0),
            mentions=value.get("mentions", 0),
        )
    return DiffState(
        version=int(raw.get("version", STATE_VERSION)),
        updated_at=datetime.fromisoformat(str(raw["updated_at"])),
        prs=prs,
    )


def save_state(path: Path, state: DiffState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": state.version,
        "updated_at": state.updated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "prs": {
            str(number): {
                "head_sha": stored.head_sha,
                "unanswered_count": stored.unanswered_count,
                "new_commits_after_review": stored.new_commits_after_review,
                "others_reviews_after_mine": stored.others_reviews_after_mine,
                "title": stored.title,
                "url": stored.url,
                "author": stored.author,
                "threads_started": stored.threads_started,
                "threads_joined": stored.threads_joined,
                "mentions": stored.mentions,
            }
            for number, stored in sorted(state.prs.items())
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build_diff_state(snapshots: list[PRSnapshot]) -> DiffState:
    return DiffState(
        version=STATE_VERSION,
        updated_at=datetime.now(tz=UTC),
        prs={snapshot.number: snapshot_to_stored(snapshot) for snapshot in snapshots},
    )


def clear_state(path: Path) -> bool:
    """Delete one state file; remove state dir if empty."""
    if not path.exists():
        return False
    path.unlink()
    with contextlib.suppress(OSError):
        path.parent.rmdir()
    return True


def clear_all_state() -> list[Path]:
    root = state_dir()
    if not root.is_dir():
        return []
    return [path for path in sorted(root.glob("*.json")) if clear_state(path)]
