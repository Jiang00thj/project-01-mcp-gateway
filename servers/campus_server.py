"""Server A: campus data, backed by the SQLite store in `datasource/campus_db.py`.

The server owns MCP concerns only — schemas, risk hints, error mapping. Every
query lives in the data layer, so swapping SQLite for a real 教务系统 client is a
one-file change.

    python -m servers.campus_server
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datasource import campus_db  # noqa: E402
from mcp_min.protocol import ToolError  # noqa: E402
from mcp_min.server import MCPServer  # noqa: E402

CONN = campus_db.open_db()

server = MCPServer(
    name="campus-server",
    version="0.2.0",
    instructions=(
        "校园数据服务：课程、成绩、空教室、选退课。"
        "成绩类工具涉及隐私，属于敏感读；选退课是写操作，执行前必须取得用户明确同意。"
    ),
)


@server.tool(
    name="search_courses",
    title="检索课程",
    description="按关键词检索课程，返回课程号、名称、教师、学分、学期与余量。",
    risk="read",
    annotations={"readOnlyHint": True},
    input_schema={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "maxLength": 64, "description": "课程名、教师名或课程号关键词"},
            "term": {"type": "string", "maxLength": 16, "description": "学期，例如 2026春"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
        },
        "required": ["keyword"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "courses": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["count", "courses"],
    },
)
def search_courses(keyword, term=None, limit=10):
    rows = campus_db.search_courses(CONN, keyword, term=term, limit=limit)
    return {"count": len(rows), "courses": rows}


@server.tool(
    name="get_grades",
    title="查询成绩",
    description="查询指定学生的成绩单。涉及个人隐私，仅在用户明确授权后调用。",
    risk="read",
    annotations={"readOnlyHint": True, "sensitive": True},
    input_schema={
        "type": "object",
        "properties": {"student_id": {"type": "string", "pattern": "^\\d{7,12}$", "description": "学号"}},
        "required": ["student_id"],
        "additionalProperties": False,
    },
)
def get_grades(student_id):
    rows = campus_db.get_grades(CONN, student_id)
    if not rows:
        raise ToolError(f"学号 {student_id} 无成绩记录，请确认学号是否正确。")
    return {"student_id": student_id, "courses": rows}


@server.tool(
    name="compute_gpa",
    title="计算加权绩点",
    description="按学分加权计算 GPA（4.0 制：score/100*4，60 分以下记 0）。",
    risk="read",
    annotations={"readOnlyHint": True, "sensitive": True},
    input_schema={
        "type": "object",
        "properties": {"student_id": {"type": "string", "pattern": "^\\d{7,12}$"}},
        "required": ["student_id"],
        "additionalProperties": False,
    },
)
def compute_gpa(student_id):
    summary = campus_db.compute_gpa(CONN, student_id)
    if summary is None:
        raise ToolError(f"学号 {student_id} 无成绩记录。")
    return {"student_id": student_id, **summary}


@server.tool(
    name="room_availability",
    title="查询空教室",
    description="查询某教学楼某天的空闲教室与座位数。",
    risk="read",
    annotations={"readOnlyHint": True},
    input_schema={
        "type": "object",
        "properties": {
            "building": {"type": "string", "maxLength": 32},
            "date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$", "description": "YYYY-MM-DD"},
        },
        "required": ["building", "date"],
        "additionalProperties": False,
    },
)
def room_availability(building, date):
    rows = campus_db.free_rooms(CONN, building, date)
    return {"building": building, "date": date, "free_rooms": rows}


@server.tool(
    name="submit_course_change",
    title="提交选退课申请",
    description="提交选课或退课申请。会写入教务系统，属于不可撤销操作。",
    risk="write",
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    input_schema={
        "type": "object",
        "properties": {
            "student_id": {"type": "string", "pattern": "^\\d{7,12}$"},
            "course_id": {"type": "string", "maxLength": 16},
            "action": {"type": "string", "enum": ["enroll", "drop"]},
            "confirm": {"type": "boolean", "description": "用户已明确确认"},
        },
        "required": ["student_id", "course_id", "action", "confirm"],
        "additionalProperties": False,
    },
)
def submit_course_change(student_id, course_id, action, confirm):
    if not confirm:
        raise ToolError("用户未确认，拒绝执行写操作。请先向用户复述影响并取得同意。")
    try:
        enrolled = campus_db.apply_course_change(CONN, student_id, course_id, action)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    return {"ok": True, "course_id": course_id, "action": action,
            "student_id": student_id, "enrolled": enrolled}


if __name__ == "__main__":
    server.serve_stdio()
