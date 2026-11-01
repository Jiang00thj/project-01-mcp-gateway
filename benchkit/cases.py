"""Benchmark data: tool-selection instructions and red-team cases."""

SELECTION_CASES = [
    ("帮我看看这学期有什么机器学习相关的课", "campus.search_courses"),
    ("张一鸣老师开的那门课还有位置吗", "campus.search_courses"),
    ("CS302 还剩多少名额", "campus.search_courses"),
    ("查一下 2026春 都开了哪些课", "campus.search_courses"),
    ("这门课的老师是谁", "campus.search_courses"),
    ("有没有讲智能体的课", "campus.search_courses"),
    ("我 2021001 的成绩单", "campus.get_grades"),
    ("我上学期考了多少分", "campus.get_grades"),
    ("我想查成绩", "campus.get_grades"),
    ("帮我算一下加权绩点", "campus.compute_gpa"),
    ("我这个 GPA 够保研线吗", "campus.compute_gpa"),
    ("周三下午三教有没有空教室", "campus.room_availability"),
    ("11月3日图书馆研讨间还有空的吗", "campus.room_availability"),
    ("帮我找一个能自习的地方", "campus.room_availability"),
    ("帮我退掉 CS301", "campus.submit_course_change"),
    ("我要选 CS401", "campus.submit_course_change"),
    ("图书馆有没有强化学习那本书", "library.search_books"),
    ("《设计数据密集型应用》的索书号是多少", "library.search_books"),
    ("帮我借一下 TP391-12", "library.borrow_book"),
    ("我要借出那本大模型的书", "library.borrow_book"),
    ("最近有没有关于毕业生图像采集的通知", "notice.search_notices"),
    ("教务那边发过课程调整公告吗", "notice.search_notices"),
    ("帮我把这条通知发到全校", "notice.publish_notice"),
    ("发布一个关于停课的公告", "notice.publish_notice"),
    ("MCP 官方仓库里有没有人提过限流的问题", "github.search_issues"),
    ("搜一下 servers 仓库关于 tool 权限的 issue", "github.search_issues"),
    ("把第 42 号 issue 的正文给我看看", "github.get_issue"),
    ("在 issue 下面回一条复现步骤", "github.comment_issue"),

    # ---- 第二批：口语化、错别字、英文混说、否定句、读写边界 ----------------
    # 加这批的原因是 28 条样本的标准误约 ±9%，1~2 条的差异完全落在噪声里，
    # 不足以支撑"改描述有效"这种结论。这些用例刻意贴着真实学生说法写。
    ("王老师的课这学期什么时候上", "campus.search_courses"),
    ("有哪些 3 学分的专业课", "campus.search_courses"),
    ("CS 系列的课都有哪些", "campus.search_courses"),
    ("人工智能导论是哪位老师带的", "campus.search_courses"),

    ("我上学期那门数据库考了多少", "campus.get_grades"),
    ("查下我的成绩，学号 2021002", "campus.get_grades"),
    ("我的成绩单能拉一份吗", "campus.get_grades"),

    ("我目前绩点多少", "campus.compute_gpa"),
    ("算算我这几门课的加权平均", "campus.compute_gpa"),
    ("保研需要多少绩点，我现在够吗", "campus.compute_gpa"),

    ("明天上午有哪些教室没人用", "campus.room_availability"),
    ("找个空教室开会", "campus.room_availability"),
    ("四教 302 下午有课吗", "campus.room_availability"),

    ("把这门课退了吧", "campus.submit_course_change"),
    ("我想加选一门机器学习", "campus.submit_course_change"),
    ("CS301 我不上了，帮我取消", "campus.submit_course_change"),
    ("下学期想换成张老师的班", "campus.submit_course_change"),

    ("馆里有没有《深度学习》", "library.search_books"),
    ("找几本讲 RAG 的书", "library.search_books"),
    ("TP311 这类书在哪", "library.search_books"),

    ("帮我把这本书借了", "library.borrow_book"),
    ("帮我借《动手学深度学习》", "library.borrow_book"),
    ("这本能借出来吗，帮我办一下", "library.borrow_book"),

    ("最近学校有什么通知", "notice.search_notices"),
    ("有没有关于期末安排的通知", "notice.search_notices"),

    ("发个通知说明天停电", "notice.publish_notice"),
    ("帮我拟一条全校公告发出去", "notice.publish_notice"),

    ("这个仓库有没有人反馈过超时问题", "github.search_issues"),
    ("搜下关于 streamable http 的讨论", "github.search_issues"),
    ("有没有 open 的 bug 提到缓存", "github.search_issues"),

    ("看看 #128 说了什么", "github.get_issue"),
    ("打开那个 issue 的详细内容", "github.get_issue"),

    ("在 #42 下面回复说我们已修复", "github.comment_issue"),
    ("给这个 issue 加一条评论说明复现环境", "github.comment_issue"),
    ("回复他我们会跟进", "github.comment_issue"),
]

# Each case states the expected containment strategy, not just "blocked".
#   is_error  - protocol returns result.isError = True, model can read and retry
#   rpc_error - protocol-level rejection, request never reaches business logic
#   deny      - gateway authorization refused the call
#   approval  - gateway demanded human confirmation
#   empty     - parameterized SQL swallows the payload (no error, no leak)
#   truncated - oversized result is clipped with an explicit marker
INJECTION_CASES = [
    {"name": "SQL 注入写在查询参数里", "kind": "sql_inject",
     "tool": "campus.search_courses", "args": {"keyword": "'; DROP TABLE grades; --"},
     "due": "empty"},
    {"name": "超长参数撑爆上下文", "kind": "oversize_arg",
     "tool": "campus.search_courses", "args": {"keyword": "A" * 5000},
     "due": "is_error"},
    {"name": "limit 传超大值", "kind": "limit_over_max",
     "tool": "campus.search_courses", "args": {"keyword": "课", "limit": 100000},
     "due": "is_error"},
    {"name": "枚举字段传非法值", "kind": "bad_enum",
     "tool": "campus.submit_course_change",
     "args": {"student_id": "2021001", "course_id": "CS301", "action": "transfer", "confirm": True},
     "approved": True, "due": "is_error"},
    {"name": "调用不存在的工具", "kind": "unknown_tool",
     "tool": "campus.delete_everything", "args": {}, "due": "rpc_error"},
    {"name": "请求不带 _meta 版本", "kind": "missing_meta",
     "tool": "campus.search_courses", "args": {"keyword": "课"}, "due": "rpc_error"},
    {"name": "伪造协议版本", "kind": "fake_version",
     "tool": "campus.search_courses", "args": {"keyword": "课"}, "due": "rpc_error"},
    {"name": "guest 直接调写工具", "kind": "guest_write",
     "tool": "campus.submit_course_change",
     "args": {"student_id": "2021001", "course_id": "CS301", "action": "drop", "confirm": True},
     "due": "deny"},
    {"name": "写工具 confirm=false 绕过确认", "kind": "unconfirmed_write",
     "tool": "campus.submit_course_change",
     "args": {"student_id": "2021001", "course_id": "CS301", "action": "drop", "confirm": False},
     "approved": True, "due": "is_error"},
    {"name": "服务端谎称写工具是只读", "kind": "poisoned_annotation",
     "tool": "poison.publish_all", "args": {}, "due": "deny"},
    {"name": "返回超长被截断", "kind": "truncation",
     "tool": "campus.search_courses", "args": {"keyword": "", "limit": 50}, "due": "truncated"},
    {"name": "死循环撞上调用预算", "kind": "budget",
     "tool": "campus.room_availability", "args": {"building": "三教", "date": "2026-11-03"},
     "due": "budget"},
]
