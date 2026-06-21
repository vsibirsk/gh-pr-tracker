"""Argparse CLI entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from rich.console import Console

from gh_pr_tracker.config import (
    TrackerConfig,
    apply_watch_label_changes,
    apply_watch_prs_changes,
    clean_all_profiles,
    ensure_profile,
    list_profile_keys,
    load_config,
    parse_profile_key,
    profile_key,
    profile_state_path,
    remove_profile,
    save_config,
)
from gh_pr_tracker.github import GitHubClient
from gh_pr_tracker.model import (
    DEFAULT_REPO,
    CollectConfig,
    PRFetchRequest,
    StatusRunConfig,
    WatchChangeConfig,
    WatchLabelChangeConfig,
)
from gh_pr_tracker.render import (
    FetchProgress,
    event_to_json,
    print_json,
    render_diff,
    render_pr_detail,
    render_profile_header,
    render_status_table,
    snapshot_to_json,
)
from gh_pr_tracker.state import build_diff_state, clear_all_state, clear_state, load_state, save_state
from gh_pr_tracker.tracker import (
    collect_snapshots,
    diff_snapshots,
    fetch_pr_detail_any_state,
    parse_repo,
)

console = Console()


@dataclass(frozen=True)
class _WatchListUpdate:
    watch: list[int]
    unwatch: list[int]
    watch_labels: list[str]
    unwatch_labels: list[str]


@dataclass(frozen=True)
class _ProfileRun:
    repo: str
    login: str
    config: StatusRunConfig
    tracker_config: TrackerConfig
    state_path: Path
    show_header: bool


def _parse_pr_list(value: str | None) -> list[int]:
    if not value:
        return []
    numbers: list[int] = []
    for raw in value.split(","):
        item = raw.strip()
        if item:
            numbers.append(int(item))
    return numbers


def _parse_label_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _exit(message: str, *, code: int = 1) -> NoReturn:
    console.print(f"[red]{message}[/red]")
    raise SystemExit(code)


def _reject_conflicting_flags(*, condition: bool, message: str) -> None:
    if condition:
        _exit(message)


async def _resolve_user(client: GitHubClient, user: str | None) -> str:
    if user:
        return user
    profile = await client.get_authenticated_user()
    login = profile.get("login")
    if not login:
        msg = "Could not determine authenticated GitHub username."
        raise ValueError(msg)
    return str(login)


async def _resolve_login(user: str | None) -> str:
    if user:
        return user
    client = GitHubClient()
    if not client.is_authenticated:
        _exit("Set --user or GITHUB_TOKEN to identify the profile.")
    return await _resolve_user(client, user)


async def _validate_watch_pr(
    client: GitHubClient,
    *,
    repo: str,
    number: int,
) -> None:
    owner, repo_name = parse_repo(repo)
    await client.get_pr_details(owner, repo_name, number)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Track GitHub PRs that need your attention",
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"Repository owner/name (default: {DEFAULT_REPO})",
    )
    parser.add_argument("--user", help="GitHub username (default: authenticated login)")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument("--state-file", type=Path, help="Override path to diff state file")
    parser.add_argument("--all", action="store_true", help="Run or clean all configured profiles")
    parser.add_argument("--diff", action="store_true", help="Show changes since last run")
    parser.add_argument("--watch-pr", dest="watch", help="Comma-separated PR numbers to watch")
    parser.add_argument("--unwatch-pr", dest="unwatch", help="Comma-separated PR numbers to remove from watch list")
    parser.add_argument("--watch-label", help="Comma-separated labels to watch (OR semantics)")
    parser.add_argument("--unwatch-label", help="Comma-separated labels to remove from watch list")
    parser.add_argument("--no-state", action="store_true", help="Do not read or write diff state")

    subparsers = parser.add_subparsers(dest="command")

    pr_parser = subparsers.add_parser("pr", help="Deep dive on one pull request")
    pr_parser.add_argument("number", type=int, help="Pull request number")

    profile_parser = subparsers.add_parser("profile", help="Manage configured profiles")
    profile_sub = profile_parser.add_subparsers(dest="profile_command", required=True)
    profile_sub.add_parser("list", help="List configured profiles")

    watch_parser = subparsers.add_parser("watch", help="Manage watched pull requests")
    watch_sub = watch_parser.add_subparsers(dest="watch_command", required=True)
    watch_sub.add_parser("list", help="List watched PR numbers and labels")
    watch_add = watch_sub.add_parser("add", help="Add PR numbers to watch list")
    watch_add.add_argument("numbers", type=int, nargs="+", help="PR numbers to watch")
    watch_remove = watch_sub.add_parser("remove", help="Remove PR numbers from watch list")
    watch_remove.add_argument("numbers", type=int, nargs="+", help="PR numbers to unwatch")

    watch_label = watch_sub.add_parser("label", help="Manage watched labels")
    watch_label_sub = watch_label.add_subparsers(dest="watch_label_command", required=True)
    watch_label_sub.add_parser("list", help="List watched labels")
    watch_label_add = watch_label_sub.add_parser("add", help="Add labels to watch list")
    watch_label_add.add_argument("labels", nargs="+", help="Labels to watch")
    watch_label_remove = watch_label_sub.add_parser("remove", help="Remove labels from watch list")
    watch_label_remove.add_argument("labels", nargs="+", help="Labels to unwatch")

    state_parser = subparsers.add_parser("state", help="Manage diff state files")
    state_sub = state_parser.add_subparsers(dest="state_command", required=True)
    state_clean = state_sub.add_parser("clean", help="Delete diff state for current or selected profile")
    state_clean.add_argument("--all", action="store_true", help="Delete all diff state files")

    config_parser = subparsers.add_parser("config", help="Manage tracker configuration")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_clean = config_sub.add_parser("clean", help="Remove profile entries from config")
    config_clean.add_argument("--all", action="store_true", help="Remove all profiles from config")

    return parser


def _clean_all_flag(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "all", False))


def _state_path_for_profile(*, repo: str, user: str, override: Path | None) -> Path:
    if override is not None:
        return override
    return profile_state_path(repo=repo, user=user)


def _config_watch_lists(
    config: TrackerConfig,
    *,
    repo: str,
    user: str,
    update: _WatchListUpdate,
) -> tuple[list[int], list[str], bool]:
    key = profile_key(repo=repo, user=user)
    created = key not in config.profiles
    settings = ensure_profile(config, repo=repo, user=user)
    watched_prs = apply_watch_prs_changes(settings.watched_prs, add=update.watch, remove=update.unwatch)
    watched_labels = apply_watch_label_changes(
        settings.watched_labels,
        add=update.watch_labels,
        remove=update.unwatch_labels,
    )
    changed = (
        created
        or watched_prs != settings.watched_prs
        or watched_labels != settings.watched_labels
    )
    settings.watched_prs = watched_prs
    settings.watched_labels = watched_labels
    return watched_prs, watched_labels, changed


async def _run_single_profile(run: _ProfileRun) -> dict[str, object] | None:
    watched_prs, watched_labels, config_changed = _config_watch_lists(
        run.tracker_config,
        repo=run.repo,
        user=run.login,
        update=_WatchListUpdate(
            watch=run.config.watch,
            unwatch=run.config.unwatch,
            watch_labels=run.config.watch_labels,
            unwatch_labels=run.config.unwatch_labels,
        ),
    )
    if config_changed:
        save_config(run.tracker_config)

    previous = None if run.config.no_state else load_state(run.state_path)

    with FetchProgress(enabled=not run.config.json_output and not run.config.all_profiles) as progress:
        snapshots = await collect_snapshots(
            GitHubClient(),
            CollectConfig(
                repo=run.repo,
                login=run.login,
                watched=watched_prs,
                watched_labels=watched_labels,
            ),
            on_progress=progress.update,
        )

    events = diff_snapshots(previous=previous, current=snapshots) if run.config.diff else []

    if not run.config.no_state:
        save_state(run.state_path, build_diff_state(snapshots))

    key = profile_key(repo=run.repo, user=run.login)
    if run.config.json_output:
        payload: dict[str, object] = {
            "profile": key,
            "state_file": str(run.state_path),
        }
        if run.config.diff:
            payload["events"] = [event_to_json(event) for event in events]
        else:
            payload["snapshots"] = [snapshot_to_json(snapshot) for snapshot in snapshots]
        return payload

    if run.show_header:
        render_profile_header(key)
    if run.config.diff:
        render_diff(events)
    else:
        render_status_table(snapshots)
    return None


async def _run_status(config: StatusRunConfig) -> None:
    client = GitHubClient()
    if not client.is_authenticated:
        _exit("GITHUB_TOKEN is not set.")

    tracker_config = load_config()

    if config.all_profiles:
        keys = list_profile_keys(tracker_config)
        if not keys:
            if config.json_output:
                print_json([])
            else:
                console.print("No configured profiles. Run for a single repo first or use watch add.")
            return
        results: list[dict[str, object]] = []
        for index, key in enumerate(keys):
            repo, login = parse_profile_key(key)
            state_path = _state_path_for_profile(repo=repo, user=login, override=None)
            if index and not config.json_output:
                console.print()
            payload = await _run_single_profile(
                _ProfileRun(
                    repo=repo,
                    login=login,
                    config=config,
                    tracker_config=tracker_config,
                    state_path=state_path,
                    show_header=not config.json_output,
                ),
            )
            if payload is not None:
                results.append(payload)
        if config.json_output:
            print_json(results)
        return

    try:
        login = await _resolve_user(client, config.user)
    except ValueError as exc:
        _exit(str(exc))

    state_path = _state_path_for_profile(repo=config.repo, user=login, override=config.state_file)
    await _run_single_profile(
        _ProfileRun(
            repo=config.repo,
            login=login,
            config=config,
            tracker_config=tracker_config,
            state_path=state_path,
            show_header=False,
        ),
    )


async def _run_pr(
    *,
    number: int,
    repo: str,
    user: str | None,
    json_output: bool,
) -> None:
    client = GitHubClient()
    if not client.is_authenticated:
        _exit("GITHUB_TOKEN is not set.")

    try:
        login = await _resolve_user(client, user)
    except ValueError as exc:
        _exit(str(exc))

    owner, repo_name = parse_repo(repo)
    with FetchProgress(enabled=not json_output) as progress:
        progress.update(f"Fetching PR #{number}…")
        snapshot = await fetch_pr_detail_any_state(
            client,
            PRFetchRequest(
                owner=owner,
                repo=repo_name,
                number=number,
                login=login,
                roles=set(),
            ),
        )

    if snapshot is None:
        _exit(f"PR #{number} not found in {repo}.")

    if json_output:
        print_json(snapshot_to_json(snapshot))
    else:
        render_pr_detail(snapshot)


async def _run_watch_change(config: WatchChangeConfig) -> None:
    client = GitHubClient()
    if not client.is_authenticated:
        _exit("GITHUB_TOKEN is not set.")

    try:
        login = await _resolve_user(client, config.user)
    except ValueError as exc:
        _exit(str(exc))

    for number in config.add:
        try:
            await _validate_watch_pr(client, repo=config.repo, number=number)
        except ValueError as exc:
            _exit(f"PR #{number} invalid: {exc}")

    tracker_config = load_config()
    settings = ensure_profile(tracker_config, repo=config.repo, user=login)
    settings.watched_prs = apply_watch_prs_changes(settings.watched_prs, add=config.add, remove=config.remove)
    save_config(tracker_config)
    console.print(
        f"Watched PRs: {', '.join(str(number) for number in settings.watched_prs) or '(none)'}",
    )


async def _run_watch_label_change(config: WatchLabelChangeConfig) -> None:
    client = GitHubClient()
    if not client.is_authenticated:
        _exit("GITHUB_TOKEN is not set.")

    try:
        login = await _resolve_user(client, config.user)
    except ValueError as exc:
        _exit(str(exc))

    tracker_config = load_config()
    settings = ensure_profile(tracker_config, repo=config.repo, user=login)
    settings.watched_labels = apply_watch_label_changes(
        settings.watched_labels,
        add=config.add,
        remove=config.remove,
    )
    save_config(tracker_config)
    console.print(f"Watched labels: {', '.join(settings.watched_labels) or '(none)'}")


def _run_profile_list(*, json_output: bool) -> None:
    tracker_config = load_config()
    keys = list_profile_keys(tracker_config)
    if json_output:
        print_json(
            [
                {
                    "profile": key,
                    "watched_prs": tracker_config.profiles[key].watched_prs,
                    "watched_labels": tracker_config.profiles[key].watched_labels,
                    "has_state": profile_state_path(
                        repo=repo_part,
                        user=user_part,
                    ).exists(),
                }
                for key in keys
                for repo_part, user_part in [parse_profile_key(key)]
            ],
        )
        return
    if not keys:
        console.print("No configured profiles.")
        return
    for key in keys:
        repo_part, user_part = parse_profile_key(key)
        settings = tracker_config.profiles[key]
        state_exists = profile_state_path(repo=repo_part, user=user_part).exists()
        state_note = "state" if state_exists else "no state"
        console.print(
            f"{key}  "
            f"prs={','.join(str(number) for number in settings.watched_prs) or '-'}  "
            f"labels={','.join(settings.watched_labels) or '-'}  "
            f"({state_note})",
        )


async def _run_state_clean(
    *,
    repo: str,
    user: str | None,
    state_file: Path | None,
    clean_all: bool,
    json_output: bool,
) -> None:
    if clean_all:
        removed = clear_all_state()
        if json_output:
            print_json({"removed": [str(path) for path in removed], "count": len(removed)})
        elif removed:
            console.print(f"Removed {len(removed)} state file(s).")
        else:
            console.print("No state files to remove.")
        return

    login = await _resolve_login(user)
    path = _state_path_for_profile(repo=repo, user=login, override=state_file)
    deleted = clear_state(path)
    if json_output:
        print_json({"profile": profile_key(repo=repo, user=login), "path": str(path), "removed": deleted})
    elif deleted:
        console.print(f"Removed state file: {path}")
    else:
        console.print(f"No state file at: {path}")


async def _run_config_clean(
    *,
    repo: str,
    user: str | None,
    clean_all: bool,
    json_output: bool,
) -> None:
    tracker_config = load_config()
    if clean_all:
        removed_keys = list_profile_keys(tracker_config)
        if not clean_all_profiles(tracker_config):
            if json_output:
                print_json({"removed": [], "count": 0})
            else:
                console.print("No profiles in config.")
            return
        save_config(tracker_config)
        if json_output:
            print_json({"removed": removed_keys, "count": len(removed_keys)})
        else:
            console.print(f"Removed {len(removed_keys)} profile(s) from config.")
        return

    login = await _resolve_login(user)
    key = profile_key(repo=repo, user=login)
    if not remove_profile(tracker_config, repo=repo, user=login):
        if json_output:
            print_json({"profile": key, "removed": False})
        else:
            console.print(f"No config entry for profile: {key}")
        return
    save_config(tracker_config)
    state_path = profile_state_path(repo=repo, user=login)
    note = f" State file still at {state_path}; run state clean --repo {repo} to remove."
    if json_output:
        print_json({"profile": key, "removed": True, "state_file": str(state_path)})
    else:
        console.print(f"Removed config for profile: {key}.{note}")


def _run_watch_list(*, repo: str, user: str | None, json_output: bool) -> None:
    login = asyncio.run(_resolve_login(user))
    tracker_config = load_config()
    key = profile_key(repo=repo, user=login)
    settings = tracker_config.profiles.get(key)
    watched_prs = settings.watched_prs if settings else []
    watched_labels = settings.watched_labels if settings else []
    if json_output:
        print_json(
            {
                "profile": key,
                "watched_prs": watched_prs,
                "watched_labels": watched_labels,
            },
        )
    else:
        console.print(f"Watched PRs: {', '.join(str(number) for number in watched_prs) or '(none)'}")
        console.print(f"Watched labels: {', '.join(watched_labels) or '(none)'}")


def _run_watch_label_list(*, repo: str, user: str | None, json_output: bool) -> None:
    login = asyncio.run(_resolve_login(user))
    tracker_config = load_config()
    key = profile_key(repo=repo, user=login)
    settings = tracker_config.profiles.get(key)
    watched_labels = settings.watched_labels if settings else []
    if json_output:
        print_json({"watched_labels": watched_labels})
    else:
        console.print(f"Watched labels: {', '.join(watched_labels) or '(none)'}")


def _run_watch_label_command(
    args: argparse.Namespace,
    *,
    repo: str,
    user: str | None,
    json_output: bool,
) -> None:
    if args.watch_label_command == "list":
        _run_watch_label_list(repo=repo, user=user, json_output=json_output)
        return
    asyncio.run(
        _run_watch_label_change(
            WatchLabelChangeConfig(
                repo=repo,
                user=user,
                add=[] if args.watch_label_command == "remove" else args.labels,
                remove=args.labels if args.watch_label_command == "remove" else [],
            ),
        ),
    )


def _run_watch_command(
    args: argparse.Namespace,
    *,
    repo: str,
    user: str | None,
    json_output: bool,
) -> bool:
    if args.watch_command == "list":
        _run_watch_list(repo=repo, user=user, json_output=json_output)
        return True
    if args.watch_command == "add":
        asyncio.run(
            _run_watch_change(
                WatchChangeConfig(
                    repo=repo,
                    user=user,
                    add=args.numbers,
                    remove=[],
                ),
            ),
        )
        return True
    if args.watch_command == "remove":
        asyncio.run(
            _run_watch_change(
                WatchChangeConfig(
                    repo=repo,
                    user=user,
                    add=[],
                    remove=args.numbers,
                ),
            ),
        )
        return True
    if args.watch_command == "label":
        _run_watch_label_command(args, repo=repo, user=user, json_output=json_output)
        return True
    return False


def _validate_status_flags(args: argparse.Namespace) -> None:
    if not args.all:
        return
    _reject_conflicting_flags(
        condition=any(
            (
                args.watch,
                args.unwatch,
                args.watch_label,
                args.unwatch_label,
                args.no_state,
                args.state_file is not None,
            ),
        ),
        message="--all cannot be combined with --watch-pr, --unwatch-pr, --watch-label, "
        "--unwatch-label, --no-state, or --state-file.",
    )


def _validate_clean_flags(args: argparse.Namespace, *, command: str) -> None:
    if not args.all:
        return
    _reject_conflicting_flags(
        condition=args.state_file is not None or args.repo != DEFAULT_REPO or args.user is not None,
        message=f"--all cannot be combined with --repo, --user, or --state-file on {command} clean.",
    )


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    json_output = args.json

    if args.command is None:
        _validate_status_flags(args)
        asyncio.run(
            _run_status(
                StatusRunConfig(
                    diff=args.diff,
                    repo=args.repo,
                    user=args.user,
                    watch=_parse_pr_list(args.watch),
                    unwatch=_parse_pr_list(args.unwatch),
                    watch_labels=_parse_label_list(args.watch_label),
                    unwatch_labels=_parse_label_list(args.unwatch_label),
                    json_output=json_output,
                    state_file=args.state_file,
                    no_state=args.no_state,
                    all_profiles=args.all,
                ),
            ),
        )
        return

    if args.command == "pr":
        asyncio.run(
            _run_pr(
                number=args.number,
                repo=args.repo,
                user=args.user,
                json_output=json_output,
            ),
        )
        return

    if args.command == "profile" and args.profile_command == "list":
        _run_profile_list(json_output=json_output)
        return

    if args.command == "watch" and _run_watch_command(
        args,
        repo=args.repo,
        user=args.user,
        json_output=json_output,
    ):
        return

    if args.command == "state" and args.state_command == "clean":
        _validate_clean_flags(args, command="state")
        asyncio.run(
            _run_state_clean(
                repo=args.repo,
                user=args.user,
                state_file=args.state_file,
                clean_all=_clean_all_flag(args),
                json_output=json_output,
            ),
        )
        return

    if args.command == "config" and args.config_command == "clean":
        _validate_clean_flags(args, command="config")
        asyncio.run(
            _run_config_clean(
                repo=args.repo,
                user=args.user,
                clean_all=_clean_all_flag(args),
                json_output=json_output,
            ),
        )
        return

    parser.error(f"Unknown command: {args.command!r}")


def app() -> None:
    """Console script entrypoint."""
    try:
        main()
    except SystemExit as exc:
        sys.exit(exc.code if exc.code is not None else 0)
