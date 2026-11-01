"""SQLite-backed campus data store.

Kept separate from the MCP layer on purpose: the protocol code should not know
SQL, and the SQL should be testable without spawning a subprocess. Swap this
module for a real campus API client and no other file needs to change.

Schema is created on first use and seeded once. Point CAMPUS_DB at another path
to get an isolated database (the tests do exactly that).
"""

import os
import sqlite3
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "data", "campus.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    teacher  TEXT NOT NULL,
    credits  REAL NOT NULL,
    term     TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    enrolled INTEGER NOT NULL,
    building TEXT,
    weekday  TEXT,
    slot     TEXT
);
CREATE TABLE IF NOT EXISTS grades (
    student_id TEXT NOT NULL,
    course_id  TEXT NOT NULL,
    score      REAL NOT NULL,
    PRIMARY KEY (student_id, course_id)
);
CREATE TABLE IF NOT EXISTS rooms (
    building TEXT NOT NULL,
    room     TEXT NOT NULL,
    date     TEXT NOT NULL,
    slot     TEXT NOT NULL,
    seats    INTEGER NOT NULL,
    free     INTEGER NOT NULL,
    PRIMARY KEY (building, room, date, slot)
);
CREATE TABLE IF NOT EXISTS course_changes (
    ts         TEXT NOT NULL,
    student_id TEXT NOT NULL,
    course_id  TEXT NOT NULL,
    action     TEXT NOT NULL
);
"""

# A realistic AI-major curriculum instead of three placeholder rows.
COURSES = [
    ("AI201", "人工智能导论", "王敏", 3.0, "2025秋", 120, 118, "三教", "周二", "08:00-09:40"),
    ("MA201", "概率论与数理统计", "陈璐", 4.0, "2025秋", 150, 150, "一教", "周一", "10:00-11:40"),
    ("CS101", "程序设计基础", "李响", 4.0, "2025秋", 140, 137, "实验楼", "周三", "14:00-15:40"),
    ("CS301", "机器学习", "王敏", 3.0, "2026春", 120, 112, "三教", "周一", "08:00-09:40"),
    ("CS302", "自然语言处理", "李响", 3.0, "2026春", 80, 80, "三教", "周二", "10:00-11:40"),
    ("CS303", "计算机视觉", "赵宇", 3.0, "2026春", 90, 74, "三教", "周四", "14:00-15:40"),
    ("CS401", "智能体系统", "张一鸣", 2.0, "2026春", 60, 41, "实验楼", "周五", "08:00-09:40"),
    ("CS402", "强化学习", "肖智清", 3.0, "2026春", 70, 66, "实验楼", "周三", "10:00-11:40"),
    ("CS403", "大模型应用开发", "张奇", 2.0, "2026春", 60, 60, "实验楼", "周四", "08:00-09:40"),
    ("SE201", "软件工程", "刘洋", 3.0, "2026春", 110, 98, "二教", "周五", "10:00-11:40"),
]

GRADES = [
    ("2021001", "MA201", 92.0), ("2021001", "CS101", 88.0), ("2021001", "AI201", 91.0),
    ("2021001", "CS301", 88.5), ("2021001", "CS302", 95.0), ("2021001", "CS303", 84.0),
    ("2021002", "MA201", 76.0), ("2021002", "CS101", 81.0), ("2021002", "AI201", 79.0),
    ("2021002", "CS301", 72.5), ("2021002", "CS302", 68.0), ("2021002", "CS303", 74.0),
    ("2021003", "MA201", 85.0), ("2021003", "CS101", 90.0), ("2021003", "AI201", 87.0),
    ("2021003", "CS301", 83.0), ("2021003", "CS302", 89.0), ("2021003", "CS303", 91.0),
]

ROOM_PLAN = [
    ("三教", "301", 120), ("三教", "302", 90), ("三教", "305", 60),
    ("图书馆", "研讨间A", 12), ("图书馆", "研讨间B", 8),
]
SLOTS = ["08:00-09:40", "10:00-11:40", "14:00-15:40", "16:00-17:40"]
DATES = ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07"]


def connect(path=None):
    path = path or os.environ.get("CAMPUS_DB", DEFAULT_DB)
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def seed(conn):
    """Idempotent: safe to call on every start, fills only when empty."""
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) AS n FROM courses").fetchone()["n"] == 0:
        conn.executemany("INSERT INTO courses VALUES (?,?,?,?,?,?,?,?,?,?)", COURSES)
    if conn.execute("SELECT COUNT(*) AS n FROM grades").fetchone()["n"] == 0:
        conn.executemany("INSERT INTO grades VALUES (?,?,?)", GRADES)
    if conn.execute("SELECT COUNT(*) AS n FROM rooms").fetchone()["n"] == 0:
        rows = []
        for date in DATES:
            for index, (building, room, seats) in enumerate(ROOM_PLAN):
                for slot_index, slot in enumerate(SLOTS):
                    # Deterministic occupancy so demos and tests agree forever.
                    busy = (index + slot_index + DATES.index(date)) % 3 != 0
                    rows.append((building, room, date, slot, seats, 0 if busy else 1))
        conn.executemany("INSERT INTO rooms VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


def open_db(path=None):
    return seed(connect(path))


# ---------------------------------------------------------------- queries
def search_courses(conn, keyword, term=None, limit=10):
    sql = "SELECT * FROM courses WHERE (name LIKE ? OR teacher LIKE ? OR id LIKE ?)"
    args = [f"%{keyword}%"] * 3
    if term:
        sql += " AND term = ?"
        args.append(term)
    sql += " ORDER BY term, id LIMIT ?"
    args.append(limit)
    rows = [dict(row) for row in conn.execute(sql, args)]
    for row in rows:
        row["seats_left"] = row["capacity"] - row["enrolled"]
    return rows


def get_grades(conn, student_id):
    return [
        dict(row)
        for row in conn.execute(
            "SELECT g.course_id, c.name, c.credits, c.term, g.score FROM grades g "
            "JOIN courses c ON c.id = g.course_id WHERE g.student_id = ? "
            "ORDER BY c.term, g.course_id",
            (student_id,),
        )
    ]


def compute_gpa(conn, student_id):
    rows = conn.execute(
        "SELECT c.credits, g.score FROM grades g JOIN courses c ON c.id = g.course_id "
        "WHERE g.student_id = ?",
        (student_id,),
    ).fetchall()
    if not rows:
        return None
    credits = sum(row["credits"] for row in rows)
    weighted = sum(
        row["credits"] * (0.0 if row["score"] < 60 else row["score"] / 100 * 4)
        for row in rows
    )
    return {"gpa": round(weighted / credits, 2), "credits": credits, "courses": len(rows)}


def free_rooms(conn, building, date):
    return [
        dict(row)
        for row in conn.execute(
            "SELECT room, slot, seats FROM rooms "
            "WHERE building = ? AND date = ? AND free = 1 ORDER BY room, slot",
            (building, date),
        )
    ]


def apply_course_change(conn, student_id, course_id, action):
    """Returns the new enrolled count, or raises ValueError with a user-facing reason."""
    course = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
    if course is None:
        raise ValueError(f"课程 {course_id} 不存在。")
    if action == "enroll" and course["enrolled"] >= course["capacity"]:
        raise ValueError(f"{course['name']} 已满（{course['enrolled']}/{course['capacity']}）。")
    if action == "drop" and course["enrolled"] <= 0:
        raise ValueError(f"{course['name']} 选课人数已为 0，无法退课。")
    delta = 1 if action == "enroll" else -1
    conn.execute("UPDATE courses SET enrolled = enrolled + ? WHERE id = ?", (delta, course_id))
    conn.execute(
        "INSERT INTO course_changes VALUES (?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), student_id, course_id, action),
    )
    conn.commit()
    return course["enrolled"] + delta
