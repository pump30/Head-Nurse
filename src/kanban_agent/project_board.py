import json
import logging
from typing import Optional

from .github import GitHubClient
from .models import Task, TaskStatus

logger = logging.getLogger(__name__)


class ProjectBoard:
    def __init__(self, github: GitHubClient, project_number: int):
        self.github = github
        self.project_number = project_number
        self._project_id: Optional[str] = None
        self._status_field_id: Optional[str] = None
        self._option_ids: dict[str, str] = {}
        self._owner: Optional[str] = None

    async def initialize(self) -> None:
        self._owner = await self.github.get_authenticated_user()
        await self._fetch_project_metadata()
        logger.info(
            "ProjectBoard initialized: project_id=%s, status_field=%s, options=%s",
            self._project_id, self._status_field_id, self._option_ids,
        )

    async def _fetch_project_metadata(self) -> None:
        query = """
        query($owner: String!, $number: Int!) {
          user(login: $owner) {
            projectV2(number: $number) {
              id
              field(name: "Status") {
                ... on ProjectV2SingleSelectField {
                  id
                  options {
                    id
                    name
                  }
                }
              }
            }
          }
        }
        """
        result = await self.github._run_graphql(query, {
            "owner": self._owner,
            "number": self.project_number,
        })

        project = result["data"]["user"]["projectV2"]
        self._project_id = project["id"]
        status_field = project["field"]
        self._status_field_id = status_field["id"]
        self._option_ids = {opt["name"]: opt["id"] for opt in status_field["options"]}

    async def get_items_by_status(self, status: TaskStatus) -> list[dict]:
        query = """
        query($owner: String!, $number: Int!, $cursor: String) {
          user(login: $owner) {
            projectV2(number: $number) {
              items(first: 50, after: $cursor) {
                nodes {
                  id
                  fieldValueByName(name: "Status") {
                    ... on ProjectV2ItemFieldSingleSelectValue {
                      name
                    }
                  }
                  content {
                    ... on Issue {
                      number
                      title
                      body
                      id
                    }
                  }
                }
                pageInfo {
                  hasNextPage
                  endCursor
                }
              }
            }
          }
        }
        """
        all_items = []
        cursor = None

        while True:
            variables = {"owner": self._owner, "number": self.project_number}
            if cursor:
                variables["cursor"] = cursor

            result = await self.github._run_graphql(query, variables)
            items_data = result["data"]["user"]["projectV2"]["items"]

            for item in items_data["nodes"]:
                field_value = item.get("fieldValueByName")
                item_status = field_value.get("name") if field_value else None
                if item_status == status.value and item.get("content"):
                    all_items.append(item)

            if not items_data["pageInfo"]["hasNextPage"]:
                break
            cursor = items_data["pageInfo"]["endCursor"]

        return all_items

    async def get_inbox_tasks(self) -> list[Task]:
        items = await self.get_items_by_status(TaskStatus.INBOX)
        tasks = []
        for item in items:
            content = item["content"]
            tasks.append(Task(
                issue_number=content["number"],
                issue_node_id=content["id"],
                project_item_id=item["id"],
                title=content["title"],
                body=content["body"] or "",
                status=TaskStatus.INBOX,
            ))
        return tasks

    async def move_to_status(self, item_id: str, status: TaskStatus) -> None:
        option_id = self._option_ids.get(status.value)
        if not option_id:
            raise ValueError(f"No option ID found for status: {status.value}")

        query = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId
            itemId: $itemId
            fieldId: $fieldId
            value: { singleSelectOptionId: $optionId }
          }) {
            projectV2Item { id }
          }
        }
        """
        await self.github._run_graphql(query, {
            "projectId": self._project_id,
            "itemId": item_id,
            "fieldId": self._status_field_id,
            "optionId": option_id,
        })
        logger.info("Moved item %s to %s", item_id, status.value)
