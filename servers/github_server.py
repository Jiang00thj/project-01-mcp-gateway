"""Server D: a real remote data source (GitHub REST API).

Deliberately kept thin. The interesting behaviour — cache, ETag revalidation,
rate-limit degradation — lives in `datasource/github_api.py`, so it can be unit
tested without spawning a subprocess and reused by other servers.

    python -m servers.github_server
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datasource.github_api import (  # noqa: E402
    ExternalApiError, create_comment, get_issue, search_issues,
)
from mcp_min.protocol import ToolError  # noqa: E402
from mcp_min.server import MCPServer  # noqa: E402

DEFAULT_REPO = "modelcontextprotocol/servers"

server = MCPServer(
    name="github-server",
    version="0.2.0",
    instructions=(
        "GitHub 仓库协作服务：检索 issue、读取详情、发表评论。"
        "所有调用都走带缓存的 REST 客户端；缓存命中时结果里 _meta.cache 为 hit/hit/stale。"
        "发评论属于写操作，需用户确认。"
    ),
)


@server.tool(
    name="search_issues",
    title="检索 Issue",
    description="在指定仓库中按关键词检索 issue，返回编号、标题、状态、标签与评论数。结果带 _meta.cache 说明是否命中缓存。",
    risk="read",
    annotations={"readOnlyHint": True},
    input_schema={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "maxLength": 128, "description": "检索词，支持 GitHub 搜索语法"},
            "repo": {"type": "string", "pattern": "^[\\w.-]+/[\\w.-]+$", "description": "owner/name，默认 MCP 官方 servers 仓库"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
        },
        "required": ["keyword"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "repo": {"type": "string"},
            "total": {"type": "integer"},
            "items": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["repo", "total", "items"],
    },
)
def search_issues_tool(keyword, repo=DEFAULT_REPO, limit=5):
    try:
        return search_issues(keyword, limit=limit, repo=repo)
    except ExternalApiError as exc:
        # Tool errors are model-correctable; a raised RPC error would abort the
        # trajectory. Same distinction the protocol draws for invalid arguments.
        raise ToolError(str(exc)) from None


@server.tool(
    name="get_issue",
    title="读取 Issue 详情",
    description="按编号读取 issue 正文前 1500 字符、标签、作者与评论数。",
    risk="read",
    annotations={"readOnlyHint": True},
    input_schema={
        "type": "object",
        "properties": {
            "number": {"type": "integer", "minimum": 1},
            "repo": {"type": "string", "pattern": "^[\\w.-]+/[\\w.-]+$"},
        },
        "required": ["number"],
        "additionalProperties": False,
    },
)
def get_issue_tool(number, repo=DEFAULT_REPO):
    try:
        return get_issue(number, repo=repo)
    except ExternalApiError as exc:
        raise ToolError(str(exc)) from None


@server.tool(
    name="comment_issue",
    title="评论 Issue",
    description="在 issue 下发表评论。写操作，默认 dry_run，不会真的写入。",
    risk="write",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    input_schema={
        "type": "object",
        "properties": {
            "number": {"type": "integer", "minimum": 1},
            "body": {"type": "string", "minLength": 1, "maxLength": 4000},
            "repo": {"type": "string", "pattern": "^[\\w.-]+/[\\w.-]+$"},
            "confirm": {"type": "boolean", "description": "用户已明确确认"},
        },
        "required": ["number", "body", "confirm"],
        "additionalProperties": False,
    },
)
def comment_issue_tool(number, body, confirm, repo=DEFAULT_REPO):
    dry_run = os.environ.get("GITHUB_WRITE") != "1"
    try:
        return create_comment(number, body, repo=repo, confirm=confirm, dry_run=dry_run)
    except ExternalApiError as exc:
        raise ToolError(str(exc)) from None


if __name__ == "__main__":
    server.serve_stdio()
