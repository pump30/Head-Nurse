import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 45]


class GitHubError(Exception):
    pass


class GitHubClient:
    def __init__(self, repo: str):
        self.repo = repo
        self.owner, self.repo_name = repo.split("/")

    async def _run_gh(self, args: list[str], input_data: Optional[str] = None) -> str:
        for attempt in range(MAX_RETRIES):
            proc = await asyncio.create_subprocess_exec(
                "gh", *args,
                stdin=asyncio.subprocess.PIPE if input_data else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(
                input=input_data.encode() if input_data else None
            )
            if proc.returncode == 0:
                return stdout.decode().strip()

            err_msg = stderr.decode().strip()
            if attempt < MAX_RETRIES - 1 and ("rate limit" in err_msg.lower() or "network" in err_msg.lower()):
                delay = RETRY_DELAYS[attempt]
                logger.warning("gh command failed (attempt %d), retrying in %ds: %s", attempt + 1, delay, err_msg)
                await asyncio.sleep(delay)
            else:
                raise GitHubError(f"gh command failed: {err_msg}")
        raise GitHubError("Max retries exceeded")

    async def _run_graphql(self, query: str, variables: Optional[dict] = None) -> dict:
        args = ["api", "graphql", "-f", f"query={query}"]
        if variables:
            for key, value in variables.items():
                if isinstance(value, int):
                    args.extend(["-F", f"{key}={value}"])
                else:
                    args.extend(["-f", f"{key}={value}"])
        result = await self._run_gh(args)
        return json.loads(result)

    async def list_open_issues(self, label: Optional[str] = None) -> list[dict]:
        args = ["issue", "list", "--repo", self.repo, "--state", "open", "--json",
                "number,title,body,nodeId,createdAt,labels"]
        if label:
            args.extend(["--label", label])
        result = await self._run_gh(args)
        return json.loads(result) if result else []

    async def get_issue_comments(self, number: int) -> list[dict]:
        args = ["issue", "view", str(number), "--repo", self.repo,
                "--json", "comments"]
        result = await self._run_gh(args)
        data = json.loads(result)
        return data.get("comments", [])

    async def add_comment(self, number: int, body: str) -> None:
        args = ["issue", "comment", str(number), "--repo", self.repo, "--body", body]
        await self._run_gh(args)

    async def add_comment_and_get_id(self, number: int, body: str) -> Optional[str]:
        query = """
        mutation($subjectId: ID!, $body: String!) {
          addComment(input: { subjectId: $subjectId, body: $body }) {
            commentEdge {
              node { id databaseId }
            }
          }
        }
        """
        issue_node_id = await self._get_issue_node_id(number)
        result = await self._run_graphql(query, {"subjectId": issue_node_id, "body": body})
        try:
            db_id = result["data"]["addComment"]["commentEdge"]["node"]["databaseId"]
            return str(db_id)
        except (KeyError, TypeError):
            return None

    async def edit_comment(self, comment_id: str, body: str) -> None:
        args = ["api", f"repos/{self.owner}/{self.repo_name}/issues/comments/{comment_id}",
                "-X", "PATCH", "-f", f"body={body}"]
        await self._run_gh(args)

    async def _get_issue_node_id(self, number: int) -> str:
        args = ["issue", "view", str(number), "--repo", self.repo, "--json", "id", "--jq", ".id"]
        return await self._run_gh(args)

    async def close_issue(self, number: int) -> None:
        args = ["issue", "close", str(number), "--repo", self.repo]
        await self._run_gh(args)

    async def get_authenticated_user(self) -> str:
        result = await self._run_gh(["api", "user", "--jq", ".login"])
        return result.strip()
