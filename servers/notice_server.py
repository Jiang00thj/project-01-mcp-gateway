"""Demo server C: campus notices. Deliberately exposes one admin-risk tool so the
gateway has a three-tier permission surface to enforce.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_min.protocol import ToolError  # noqa: E402
from mcp_min.server import MCPServer  # noqa: E402

NOTICES = [
    {"id": "N1001", "title": "2026届毕业生图像采集通知", "body": "11月10日前完成图像采集。", "tag": "教务"},
    {"id": "N1002", "title": "智能体系统课程调整", "body": "第9周起改至三教302。", "tag": "教务"},
]

server = MCPServer(
    name="notice-server",
    version="0.1.0",
    instructions="通知公告服务：检索与发布校内通知。",
)


@server.tool(
    name="search_notices",
    title="检索通知",
    description="按关键词或标签检索校内通知。",
    risk="read",
    annotations={"readOnlyHint": True},
    input_schema={
        "type": "object",
        "properties": {
            "keyword": {"type": "string"},
            "tag": {"type": "string"},
        },
        "additionalProperties": False,
    },
)
def search_notices(keyword="", tag=None):
    hits = [
        n for n in NOTICES
        if (not keyword or keyword in n["title"] or keyword in n["body"])
        and (not tag or n["tag"] == tag)
    ]
    return {"count": len(hits), "notices": hits}


@server.tool(
    name="publish_notice",
    title="发布通知",
    description="向全校发布通知，属于管理员操作，影响面大且不可撤回。",
    risk="admin",
    annotations={"readOnlyHint": False, "destructiveHint": True},
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
            "tag": {"type": "string", "default": "综合"},
            "confirm": {"type": "boolean"},
        },
        "required": ["title", "body", "confirm"],
        "additionalProperties": False,
    },
)
def publish_notice(title, body, tag="综合", confirm=False):
    if not confirm:
        raise ToolError("发布操作未确认，已取消。")
    notice = {"id": f"N{1000 + len(NOTICES) + 1}", "title": title, "body": body, "tag": tag}
    NOTICES.append(notice)
    return {"ok": True, "notice": notice}


if __name__ == "__main__":
    server.serve_stdio()
