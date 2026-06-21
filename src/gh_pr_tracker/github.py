"""GitHub API client."""

from __future__ import annotations

import os
from typing import Any, cast

import httpx

HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE = 422
SEARCH_MAX_PAGES = 10
SEARCH_PAGE_SIZE = 100
GRAPHQL_MAX_PAGES = 50
REQUEST_TIMEOUT_SECONDS = 30.0
ERROR_BODY_SNIPPET_LEN = 200

REVIEW_THREADS_QUERY = """
query($owner: String!, $repo: String!, $pr_number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr_number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          isResolved
          isOutdated
          comments(first: 100) {
            nodes {
              id
              url
              body
              createdAt
              author {
                login
              }
            }
          }
        }
      }
    }
  }
}
"""


class GitHubClient:
    """Async GitHub REST and GraphQL client."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.base_url = "https://api.github.com"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"
        self.is_authenticated = bool(self.token)

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        url = f"{self.base_url}{endpoint}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(
                    method,
                    url,
                    headers=self.headers,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    params=params,
                )
                response.raise_for_status()
                return cast("dict[str, Any] | list[dict[str, Any]]", response.json())
            except httpx.HTTPStatusError as exc:
                raise self._http_error(exc) from None
            except httpx.TimeoutException as exc:
                msg = "Request timed out after 30 seconds"
                raise ValueError(msg) from exc
            except httpx.ConnectError as exc:
                msg = "Failed to connect to GitHub API. Check your network connection."
                raise ValueError(msg) from exc

    @staticmethod
    def _error_message_from_response(response: httpx.Response) -> str:
        try:
            error_body = response.json()
        except (ValueError, TypeError):
            return response.text[:ERROR_BODY_SNIPPET_LEN] if response.text else "No details"
        else:
            if not isinstance(error_body, dict):
                return "Unknown error"
            error_message = str(error_body.get("message", "Unknown error"))
            error_details = error_body.get("errors", [])
            if isinstance(error_details, list) and error_details:
                details_str = "; ".join(
                    f"{err.get('field', 'unknown')}: {err.get('message', err.get('code', 'error'))}"
                    for err in error_details
                    if isinstance(err, dict)
                )
                error_message = f"{error_message} ({details_str})"
            return error_message

    def _http_error(self, exc: httpx.HTTPStatusError) -> ValueError:
        status = exc.response.status_code
        error_message = self._error_message_from_response(exc.response)

        if status == HTTP_UNAUTHORIZED:
            if not self.token:
                msg = "No GitHub token configured. Set GITHUB_TOKEN environment variable with a Personal Access Token."
            else:
                msg = f"Invalid or expired GitHub token: {error_message}"
            raise ValueError(msg) from None
        if status == HTTP_FORBIDDEN:
            msg = f"GitHub API forbidden (rate limit or permissions): {error_message}"
            raise ValueError(msg) from None
        if status == HTTP_NOT_FOUND:
            msg = f"Resource not found: {error_message}"
            raise ValueError(msg) from None
        if status == HTTP_UNPROCESSABLE:
            msg = f"GitHub API validation error: {error_message}"
            raise ValueError(msg) from None
        msg = f"GitHub API error {status}: {error_message}"
        raise ValueError(msg) from None

    async def _graphql_request(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/graphql"
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    headers=self.headers,
                    json=payload,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                result = response.json()
            except httpx.HTTPStatusError as exc:
                raise self._http_error(exc) from None
            except httpx.TimeoutException as exc:
                msg = "Request timed out after 30 seconds"
                raise ValueError(msg) from exc
            except httpx.ConnectError as exc:
                msg = "Failed to connect to GitHub API. Check your network connection."
                raise ValueError(msg) from exc

        if not isinstance(result, dict):
            msg = "GraphQL response was not a JSON object"
            raise TypeError(msg)
        if "errors" in result:
            errors = result["errors"]
            if isinstance(errors, list):
                error_messages = "; ".join(
                    str(err.get("message", "Unknown error")) for err in errors if isinstance(err, dict)
                )
            else:
                error_messages = "Unknown GraphQL error"
            msg = f"GraphQL error: {error_messages}"
            raise ValueError(msg)
        return cast("dict[str, Any]", result)

    async def get_authenticated_user(self) -> dict[str, Any]:
        result = await self._request("GET", "/user")
        return cast("dict[str, Any]", result)

    async def search_open_prs(self, repo: str, qualifier: str) -> list[dict[str, Any]]:
        query = f"is:pr is:open repo:{repo} {qualifier}"
        items: list[dict[str, Any]] = []
        page = 1
        while page <= SEARCH_MAX_PAGES:
            result = await self._request(
                "GET",
                "/search/issues",
                params={"q": query, "per_page": SEARCH_PAGE_SIZE, "page": page},
            )
            if not isinstance(result, dict):
                break
            batch = result.get("items", [])
            if not isinstance(batch, list):
                break
            items.extend(batch)
            if len(batch) < SEARCH_PAGE_SIZE:
                break
            page += 1
        return items

    async def get_pr_details(self, owner: str, repo: str, pr_number: int) -> dict[str, Any]:
        result = await self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}")
        return cast("dict[str, Any]", result)

    async def get_pr_reviews(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[dict[str, Any]]:
        result = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
        )
        return result if isinstance(result, list) else []

    async def get_pull_commits(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[dict[str, Any]]:
        result = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pr_number}/commits",
        )
        return result if isinstance(result, list) else []

    async def get_issue_comments(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[dict[str, Any]]:
        result = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
            params={"per_page": SEARCH_PAGE_SIZE},
        )
        return result if isinstance(result, list) else []

    async def get_pr_review_threads(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[dict[str, Any]]:
        all_threads: list[dict[str, Any]] = []
        cursor: str | None = None
        page = 0
        while page < GRAPHQL_MAX_PAGES:
            page += 1
            variables: dict[str, Any] = {
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "cursor": cursor,
            }
            result = await self._graphql_request(REVIEW_THREADS_QUERY, variables)
            data = result.get("data") or {}
            repository = data.get("repository") or {}
            pull_request = repository.get("pullRequest") or {}
            review_threads = pull_request.get("reviewThreads") or {}
            nodes = review_threads.get("nodes") or []
            all_threads.extend(nodes)
            page_info = review_threads.get("pageInfo") or {}
            if page_info.get("hasNextPage"):
                cursor = page_info.get("endCursor")
            else:
                break
        return all_threads
