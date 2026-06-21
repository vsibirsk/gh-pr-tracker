"""Tests for GitHub client."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from gh_pr_tracker.github import GitHubClient

if TYPE_CHECKING:
    from pytest_httpx import HTTPXMock


@pytest.mark.asyncio
async def test_get_authenticated_user(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.github.com/user",
        json={"login": "vsibirsk"},
    )
    client = GitHubClient(token="test-token")
    user = await client.get_authenticated_user()
    assert user["login"] == "vsibirsk"


@pytest.mark.asyncio
async def test_search_open_prs(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.github.com/search/issues?q=is%3Apr+is%3Aopen+repo%3Aorg%2Frepo+author%3Ame&per_page=100&page=1",
        json={"items": [{"number": 1}]},
    )
    client = GitHubClient(token="test-token")
    items = await client.search_open_prs("org/repo", "author:me")
    assert items == [{"number": 1}]


@pytest.mark.asyncio
async def test_http_error_mapping(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://api.github.com/user", status_code=401, json={"message": "Bad credentials"})
    client = GitHubClient(token="bad")
    with pytest.raises(ValueError, match="Invalid or expired"):
        await client.get_authenticated_user()
