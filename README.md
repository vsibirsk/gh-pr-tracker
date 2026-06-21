# gh-pr-tracker

Track open GitHub pull requests that need your attention — PRs you authored, reviewed, commented on, watch by number, or watch by label.

## Requirements

- Python **3.14+**
- [uv](https://docs.astral.sh/uv/)
- `GITHUB_TOKEN` with `repo` and `read:user` scopes

## Install

```bash
uv sync
# or install as a tool:
uv tool install --python 3.14 .
```

## Usage

Default repository: `RedHatQE/openshift-virtualization-tests`. Default user: authenticated GitHub login.

```bash
export GITHUB_TOKEN=ghp_...

# Snapshot table (default)
uv run gh-pr-tracker

# All configured profiles (grouped like separate runs)
uv run gh-pr-tracker --all
uv run gh-pr-tracker --all --diff

# Changes since last run
uv run gh-pr-tracker --diff

# JSON output (for scripts / automation)
uv run gh-pr-tracker --json
uv run gh-pr-tracker --diff --json

# Single PR deep dive
uv run gh-pr-tracker pr 1234

# Watch PRs by number
uv run gh-pr-tracker --watch-pr 1234,5678
uv run gh-pr-tracker --unwatch-pr 1234
uv run gh-pr-tracker watch add 1234
uv run gh-pr-tracker watch remove 1234
uv run gh-pr-tracker watch list

# Watch PRs by label (OR semantics — union of all matching open PRs)
uv run gh-pr-tracker --watch-label needs-qe,blocked
uv run gh-pr-tracker --unwatch-label needs-qe
uv run gh-pr-tracker watch label add needs-qe
uv run gh-pr-tracker watch label remove needs-qe
uv run gh-pr-tracker watch label list

# Profiles and cleanup
uv run gh-pr-tracker profile list
uv run gh-pr-tracker state clean                         # current profile diff state
uv run gh-pr-tracker state clean --repo owner/repo       # specific profile state
uv run gh-pr-tracker state clean --all                 # all diff state files
uv run gh-pr-tracker config clean                      # current profile from config
uv run gh-pr-tracker config clean --repo owner/repo
uv run gh-pr-tracker config clean --all

# Overrides
uv run gh-pr-tracker --repo owner/repo --user mylogin
uv run gh-pr-tracker --state-file /path/to/state.json  # override diff state path only
uv run gh-pr-tracker --no-state --watch-pr 1234           # one-off run, no diff baseline
```

## Output

PRs are grouped into tables by priority: **mentioned → authored → reviewed → commented → watched**.

| Column | Meaning |
|--------|---------|
| **#** | PR number (clickable link to the PR) |
| **Title** | PR title |
| **Owner** | GitHub login of the PR author |
| **Roles** | Why this PR is tracked (`author`, `reviewer`, `commenter`, `watched`) |
| **Age** | Time since the PR was opened |
| **Push** | Time since the last commit on the PR branch |
| **Unans.** | Count of unanswered threads and @mentions |
| **Flags** | Clickable attention signals (see below) |

### Flag links

Each flag in the **Flags** column is a separate link:

| Flag | Links to |
|------|----------|
| `started threads(1)` | Unresolved review thread you started |
| `joined threads(1)` | Unresolved thread you joined |
| `mention(1)` | @mention without your later reply |
| `new-commits` | Latest commit on the PR |
| `new-reviews(username)` | A review submitted after yours |

The `pr` subcommand shows the same detail in a panel, including owner, opened/last-push ages, and full URL lists.

With `--all`, each configured profile is printed under a header (`owner/repo@user`) followed by its own category tables or diff output.

## How PRs are discovered

Separate GitHub searches (never OR-combined with each other):

- `reviewed-by:YOU`
- `commenter:YOU`
- `author:YOU`

These are unioned with:

- manually watched PR numbers
- open PRs matching any watched label (each label is its own search; results are unioned)

PRs that lose a watched label drop off on the next run. Manually watched numbers stay until you remove them or the PR closes.

## Attention signals

| Signal | Meaning |
|--------|---------|
| **Started threads** | Inline review thread you opened; last reply is not yours |
| **Joined threads** | Thread someone else started where you replied; last reply is not yours |
| **@mentions** | `@your-login` in PR conversation or review threads without your later reply |
| **New commits after your review** | PR head moved since your last submitted review |
| **Others reviewed after you** | New reviews submitted after yours |

## Storage

Two files per profile scope (`owner/repo@user`):

```
~/.cache/gh-pr-tracker/
  config.json
  state/
    owner+repo@user.json
```

### Config (`config.json`)

Tracking intent — survives `state clean`:

```json
{
  "profiles": {
    "RedHatQE/openshift-virtualization-tests@vsibirsk": {
      "watched_prs": [1234],
      "watched_labels": ["needs-qe"]
    }
  }
}
```

Managed by `watch …`, `watch label …`, and auto-registration on first status run.

`config clean` removes profile entries (not diff state). Orphan state files can remain until `state clean`.

### Diff state (`state/owner+repo@user.json`)

Baseline for `--diff` only — PR SHAs, attention counts, titles:

```json
{
  "updated_at": "2026-06-17T12:00:00Z",
  "prs": { "1234": { "head_sha": "abc…", "unanswered_count": 1, … } }
}
```

`state clean` deletes diff state only. Use `--all` to wipe every file in `state/`.

Closed PRs disappear from the status table; `--diff` emits a one-time `pr_closed` event.

## JSON snapshot example

```json
{
  "number": 1234,
  "title": "Add feature",
  "url": "https://github.com/org/repo/pull/1234",
  "author": "other-dev",
  "created_at": "2026-06-01T10:00:00+00:00",
  "last_push_at": "2026-06-17T09:00:00+00:00",
  "flags": {
    "new_commits": {"active": true, "url": "https://github.com/org/repo/pull/1234/commits/abc123"},
    "new_reviews": [{"reviewer": "jdoe", "url": "https://github.com/org/repo/pull/1234#pullrequestreview-99"}]
  },
  "unanswered": {
    "started_threads": {"count": 1, "urls": ["https://github.com/org/repo/pull/1234#discussion_r1"]},
    "joined_threads": {"count": 0, "urls": []},
    "mentions": {"count": 0, "urls": []}
  }
}
```

## JSON diff event example

```json
[
  {
    "type": "new_commits",
    "pr_number": 1234,
    "title": "Add feature",
    "url": "https://github.com/org/repo/pull/1234",
    "details": {"old_sha": "abc", "new_sha": "def"},
    "at": "2026-06-17T12:00:00+00:00"
  }
]
```

## Development

```bash
uv run pytest
uv run ruff check .
uv run mypy src
pre-commit run --all-files
```
