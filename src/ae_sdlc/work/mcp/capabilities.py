"""Logical operations each provider must offer, checked against the catalog actually visible.

Tool names are pinned here and verified at runtime. Atlassian's discover is natural-language search,
so it cannot resolve them. Its deferred operations are absent from the default tools/list, so the
catalog also includes the flat "?tools=all" list and they are invoked through executeRead/executeWrite.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import McpClient, ToolResponse

DIRECT, DEFERRED_READ, DEFERRED_WRITE = "direct", "executeRead", "executeWrite"


@dataclass(frozen=True)
class Operation:
    name: str
    tool: str
    required: bool = True
    route: str = DIRECT


_OPERATIONS = {
    "linear": [
        Operation("read_issue", "get_issue"),
        Operation("search_issues", "list_issues"),
        Operation("create_issue", "save_issue"),
        Operation("update_issue", "save_issue"),
        Operation("transition_issue", "save_issue"),
        Operation("list_statuses", "list_issue_statuses"),
        Operation("read_comments", "list_comments"),
        Operation("add_comment", "save_comment"),
        Operation("read_dependencies", "get_issue"),
        Operation("create_dependencies", "save_issue", required=False),
        Operation("create_epic", "save_project", required=False),
        Operation("read_epic", "get_project"),
        Operation("list_epics", "list_projects"),
        Operation("read_team", "get_team"),
        Operation("list_labels", "list_issue_labels"),
        Operation("create_label", "save_issue_label"),
    ],
    "jira": [
        Operation("read_issue", "getJiraIssue"),
        Operation("search_issues", "searchJiraIssuesUsingJql"),
        Operation("create_issue", "createJiraIssue"),
        Operation("update_issue", "editJiraIssue"),
        Operation("transition_issue", "transitionJiraIssue"),
        Operation("list_transitions", "listJiraIssueTransitions", route=DEFERRED_READ),
        Operation("list_statuses", "listJiraStatuses", route=DEFERRED_READ),
        Operation("list_issue_types", "listJiraProjectIssueTypesMetadata", route=DEFERRED_READ),
        Operation("read_type_fields", "getJiraIssueTypeMetaWithFields", route=DEFERRED_READ),
        Operation("read_comments", "listJiraIssueComments"),
        Operation("add_comment", "addOrEditJiraIssueComment"),
        Operation("read_dependencies", "getJiraIssue"),
        Operation("create_dependencies", "createJiraIssueLink", required=False, route=DEFERRED_WRITE),
        Operation("create_epic", "createJiraIssue", required=False),
        Operation("list_projects", "listJiraProjects", required=False, route=DEFERRED_READ),
    ],
}


def operations(provider: str) -> list[Operation]:
    return _OPERATIONS[provider]


def operation(provider: str, name: str) -> Operation:
    return next(op for op in _OPERATIONS[provider] if op.name == name)


def is_available(op: Operation, catalog: set[str]) -> bool:
    return op.tool in catalog and (op.route == DIRECT or op.route in catalog)


def discover_catalog(provider: str, url: str, credential: Any, client: McpClient) -> set[str]:
    """Return every tool name visible to this identity. `client` is connected to url."""
    names = {tool.name for tool in client.list_tools()}
    if provider == "jira":
        separator = "&" if "?" in url else "?"
        with McpClient(f"{url}{separator}tools=all", credential) as flat:
            names |= {tool.name for tool in flat.list_tools()}
    return names


def invoke(client: McpClient, op: Operation, arguments: dict[str, Any], *, cloud_id: str | None = None,
           retry_safe: bool = False) -> ToolResponse:
    if op.route == DIRECT:
        return client.call(op.tool, arguments, retry_safe=retry_safe)
    return client.call(op.route, {"cloudId": cloud_id, "name": op.tool, "inputs": arguments}, retry_safe=retry_safe)
