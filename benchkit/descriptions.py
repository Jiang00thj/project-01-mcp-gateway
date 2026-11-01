"""Tool-description rewrites, used as an A/B arm in the selection benchmark.

Diagnosis that motivated this file: at 12 tools both deepseek-flash and
deepseek-v4-pro scored 85.7%, and every miss was the same shape — a *write*
tool replaced by its *read* sibling:

    submit_course_change -> search_courses
    borrow_book          -> search_books
    comment_issue        -> get_issue / search_issues

That is not a model-capability problem (a bigger model did not help). It is a
**description** problem: the read tool's description ("检索馆藏，返回索书号")
is a plausible answer to "帮我借一下 TP391-12" because the user supplied a
call number, so the model reads it as "look this up first".

Each rewrite therefore adds three things the originals lack:

  1. an explicit 【只读】/【写入】 prefix, so read vs write is visible before
     the description is read to the end;
  2. a negative constraint — "仅用于…；要 X 时改用 Y" — because telling a model
     what a tool is NOT for is what separates it from its sibling;
  3. the user's own phrasing ("借一下" / "退掉" / "回一条") as a trigger,
     because the router is matching surface forms, not doing ontology.

Nothing here changes behaviour: the gateway still derives risk from its own
registry and never trusts server-supplied annotations.
"""

IMPROVED = {
    "campus.search_courses":
        "【只读·不改变任何数据】按关键词检索课程，返回课程号、名称、教师、学分、学期与余量。"
        "仅用于查询课程信息；用户要选课、退课、改选时改用 campus.submit_course_change。",

    "campus.get_grades":
        "【只读】查询某学生的单科成绩单。用户问「考了多少分」「成绩单」「这门课几分」时用本工具；"
        "用户问绩点/GPA/保研线时改用 campus.compute_gpa。",

    "campus.compute_gpa":
        "【只读】按学分加权计算 GPA。用户问「绩点」「GPA」「加权」「够不够保研线」时用本工具。",

    "campus.room_availability":
        "【只读】查询某教学楼某天的空闲教室与座位数。用户问「哪里能自习」「有空教室吗」时用本工具；"
        "申请借用教室属于另一个流程，不在本工具范围。",

    "campus.submit_course_change":
        "【写入·不可撤销·需用户确认】提交选课或退课申请。用户说「我要选」「退掉」「换课」「报名」时用本工具，"
        "即使还没给学号也应选本工具，由工具返回缺参提示。不要先用 search_courses 代替。",

    "library.search_books":
        "【只读】检索馆藏，返回索书号与可借数量。仅用于找书；用户要借出时改用 library.borrow_book，"
        "即使他已经给出索书号也不要先调本工具。",

    "library.borrow_book":
        "【写入·库存减一】为指定学生借出一本书。用户说「借一下」「帮我借」「借出」时用本工具。",

    "notice.search_notices":
        "【只读】检索校内通知。用户问「有没有通知」「公告怎么说」时用本工具。",

    "notice.publish_notice":
        "【写入·全校可见·管理员操作】向全校发布通知。用户说「发通知」「发公告」「通知全校」时用本工具。",

    "github.search_issues":
        "【只读】按关键词检索 issue。仅用于查找；用户要在 issue 下发言时改用 github.comment_issue。",

    "github.get_issue":
        "【只读】按编号读取单个 issue 正文。仅用于阅读；用户要回复或评论时改用 github.comment_issue。",

    "github.comment_issue":
        "【写入】在 issue 下发表评论。用户说「回一条」「评论」「回复这个 issue」时用本工具。",
}


def apply(tools, arm):
    """Return a copy of `tools` with descriptions swapped according to `arm`."""
    if arm != "improved":
        return tools
    out = []
    for tool in tools:
        copy = dict(tool)
        if tool["name"] in IMPROVED:
            copy["description"] = IMPROVED[tool["name"]]
        out.append(copy)
    return out
