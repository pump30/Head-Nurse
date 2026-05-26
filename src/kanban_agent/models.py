from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Optional


class TaskStatus(Enum):
    INBOX = "Inbox"
    IN_PROGRESS = "In Progress"
    WAITING = "Waiting"
    COMPLETED = "Completed"
    FAILED = "Failed"


@dataclass
class Comment:
    id: str
    body: str
    author: str
    created_at: datetime


@dataclass
class Task:
    issue_number: int
    issue_node_id: str
    project_item_id: str
    title: str
    body: str
    status: TaskStatus
    comments: list[Comment] = field(default_factory=list)
    claude_session_id: Optional[str] = None
    last_processed_comment_id: Optional[str] = None
