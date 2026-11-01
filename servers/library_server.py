"""Demo server B: library catalogue.

Run as a subprocess over stdio:
    python -m servers.library_server
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_min.protocol import ToolError  # noqa: E402
from mcp_min.server import MCPServer  # noqa: E402

BOOKS = [
    {"id": "TP181-01", "title": "强化学习：原理与Python实现", "author": "肖智清", "stock": 3},
    {"id": "TP311-07", "title": "设计数据密集型应用", "author": "Martin Kleppmann", "stock": 1},
    {"id": "TP391-12", "title": "大规模语言模型：从理论到实践", "author": "张奇", "stock": 2},
]
LOANS = []

server = MCPServer(
    name="library-server",
    version="0.1.0",
    instructions="图书馆服务：书目检索与借阅。借阅会修改库存，属于写操作。",
)


@server.tool(
    name="search_books",
    title="检索书目",
    description="按书名或作者关键词检索馆藏，返回索书号与可借数量。",
    risk="read",
    annotations={"readOnlyHint": True},
    input_schema={
        "type": "object",
        "properties": {"keyword": {"type": "string"}},
        "required": ["keyword"],
        "additionalProperties": False,
    },
)
def search_books(keyword):
    hits = [b for b in BOOKS if keyword in b["title"] or keyword in b["author"]]
    return {"count": len(hits), "books": hits}


@server.tool(
    name="borrow_book",
    title="借阅图书",
    description="为指定学生借出一本书，库存减一。",
    risk="write",
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    input_schema={
        "type": "object",
        "properties": {
            "student_id": {"type": "string"},
            "book_id": {"type": "string"},
            "confirm": {"type": "boolean"},
        },
        "required": ["student_id", "book_id", "confirm"],
        "additionalProperties": False,
    },
)
def borrow_book(student_id, book_id, confirm):
    if not confirm:
        raise ToolError("用户未确认借阅，操作取消。")
    book = next((b for b in BOOKS if b["id"] == book_id), None)
    if book is None:
        raise ToolError(f"索书号 {book_id} 不存在。")
    if book["stock"] <= 0:
        raise ToolError(f"《{book['title']}》当前无可借库存。")
    book["stock"] -= 1
    LOANS.append({"student_id": student_id, "book_id": book_id})
    return {"ok": True, "book_id": book_id, "left": book["stock"], "due_in_days": 30}


if __name__ == "__main__":
    server.serve_stdio()
