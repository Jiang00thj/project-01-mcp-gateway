"""Tests for the description lint.

Two kinds of test live here:

  * rule tests, which build a synthetic tool and assert the specific finding
    fires — these pin the rule, not the current text;
  * arm tests, which run the lint over the real 12 tools. The improved arm must
    stay at 0 errors (that is what CI enforces), and the original arm must keep
    failing. If someone "fixes" the original descriptions in servers/, the A/B
    control group disappears and the README's numbers stop being reproducible —
    so that regression gets a test of its own.
"""

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchkit import desc_lint  # noqa: E402
from benchkit.desc_lint import ERROR, WARNING, lint, load_tools  # noqa: E402


def make(name="campus.search_courses", description="", risk="read", domain="campus"):
    return {"name": name, "description": description, "risk": risk, "domain": domain}


def codes(findings, level=ERROR):
    return {f.code for f in findings if f.level == level}


GOOD_READ = "【只读】按关键词检索课程，返回余量。仅用于查询；用户说「选课」时改用 campus.submit_course_change。"
GOOD_WRITE = "【写入·不可撤销】提交选课申请。用户说「我要选」时用本工具；不要先用 search_courses 代替。"


class RuleTests(unittest.TestCase):
    def test_clean_pair_has_no_findings(self):
        tools = [
            make("campus.search_courses", GOOD_READ, "read"),
            make("campus.submit_course_change", GOOD_WRITE, "write"),
        ]
        self.assertEqual(lint(tools), [])

    def test_empty_description(self):
        self.assertIn("E1-empty", codes(lint([make(description="")])))

    def test_too_short(self):
        code = codes(lint([make(description="【只读】查课")]))
        self.assertIn("E2-short", code)

    def test_write_needs_write_prefix(self):
        text = "【只读】其实会写数据。用户说要选课时用本工具，不要先用别的。"
        self.assertIn("E3-prefix", codes(lint([make("campus.submit_course_change", text, "write")])))

    def test_read_needs_read_prefix(self):
        text = "查询课程余量。用户说要选课时改用 campus.submit_course_change。"
        self.assertIn("E4-prefix", codes(lint([make(description=text)])))

    def test_admin_counts_as_write(self):
        text = "【只读】发布通知。用户说要发通知时用本工具，不要先用 search。"
        self.assertIn("E3-prefix", codes(lint([make("notice.publish_notice", text, "admin")])))

    def test_placeholder(self):
        text = "【只读】TODO 待补的课程检索。用户说要查课时用本工具。"
        self.assertIn("E5-placeholder", codes(lint([make(description=text)])))

    def test_missing_user_phrasing(self):
        text = "【只读】按关键词检索课程并返回课程号、名称与余量。仅用于查询信息。"
        self.assertIn("E6-trigger", codes(lint([make(description=text)])))

    def test_duplicate_description(self):
        tools = [
            make("campus.search_courses", GOOD_READ, "read"),
            make("campus.get_grades", GOOD_READ, "read"),
        ]
        self.assertIn("E7-duplicate", codes(lint(tools)))

    def test_write_without_exclusion_warns(self):
        text = "【写入·库存减一】为指定学生借出一本书。用户说「借一下」时用本工具。"
        found = codes(lint([make("library.borrow_book", text, "write")]), WARNING)
        self.assertIn("W1-no-exclusion", found)

    def test_write_without_impact_note_warns(self):
        text = "【写入】在 issue 下发表评论。用户说「回一条」时用本工具，不要先用搜索。"
        found = codes(lint([make("github.comment_issue", text, "write")]), WARNING)
        self.assertIn("W2-no-impact", found)

    def test_read_that_never_names_sibling_warns(self):
        tools = [
            make("notice.search_notices", "【只读】检索校内通知。用户问「有通知吗」时用本工具。", "read"),
            make("notice.publish_notice", GOOD_WRITE, "admin"),
        ]
        found = codes(lint(tools), WARNING)
        self.assertIn("W3-no-pointer", found)

    def test_warnings_alone_do_not_fail_the_suite(self):
        # A sole warning must not be reported as an error.
        text = "【写入·库存减一】为指定学生借出一本书。用户说「借一下」时用本工具。"
        findings = lint([make("library.borrow_book", text, "write")])
        self.assertEqual([f.level for f in findings], [WARNING])


class ArmTests(unittest.TestCase):
    def test_improved_arm_is_clean(self):
        """CI gate: the rewritten set must keep zero errors."""
        tools = load_tools("improved")
        errors = [f for f in lint(tools) if f.level is ERROR]
        self.assertEqual(errors, [], f"errors in improved arm: {errors}")

    def test_improved_arm_covers_every_tool(self):
        """A new tool without a rewrite would silently ship the original text."""
        original = load_tools("original")
        improved = {t["name"]: t["description"] for t in load_tools("improved")}
        untouched = [t["name"] for t in original
                     if improved[t["name"]] == t["description"]]
        self.assertEqual(untouched, [],
                         f"tools with no rewritten description: {untouched}")

    def test_original_arm_still_fails(self):
        """The control group must stay intact — its failures are the evidence."""
        errors = [f for f in lint(load_tools("original")) if f.level is ERROR]
        self.assertGreater(len(errors), 0,
                           "original arm is clean; the A/B control group is gone")

    def test_both_arms_see_the_same_twelve_tools(self):
        self.assertEqual(len(load_tools("original")), 12)
        self.assertEqual(len(load_tools("improved")), 12)


class CliTests(unittest.TestCase):
    def run_cli(self, argv):
        """Run main() with its report swallowed, so test output stays readable."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            return desc_lint.main(argv)

    def test_improved_exit_code_is_zero(self):
        self.assertEqual(self.run_cli(["--arm", "improved"]), 0)

    def test_original_exit_code_is_one(self):
        self.assertEqual(self.run_cli(["--arm", "original"]), 1)

    def test_strict_promotes_warnings(self):
        # improved has warnings today, so --strict must turn the run red.
        self.assertEqual(self.run_cli(["--arm", "improved", "--strict"]), 1)


if __name__ == "__main__":
    unittest.main()
