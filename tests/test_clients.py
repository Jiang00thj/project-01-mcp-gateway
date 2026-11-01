"""Client-layer tests.

A stub gateway keeps these tests fast and independent of subprocesses: the point
is the loop's control flow (mapping, approval, error surfacing), not the transport.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clients.openai_tools import (  # noqa: E402
    ScriptedModel, estimate_cost, export_tools, run_loop,
)
from gateway.gateway import (  # noqa: E402
    ApprovalRequired, BudgetExceeded, PermissionDenied,
)


class StubGateway:
    def __init__(self, tools, fail_with=None):
        self._tools = tools
        self.fail_with = fail_with or {}
        self.calls = []

    def list_tools(self, principal):
        return self._tools

    def call(self, principal, name, arguments, approved=False, timeout=10.0):
        self.calls.append({"name": name, "approved": approved, "arguments": arguments})
        error = self.fail_with.get(name)
        if error is not None and not approved:
            raise error
        return {"content": [{"type": "text", "text": '{"ok": true}'}]}


def tool(name, description="d", schema=None):
    return {"name": name, "description": description,
            "inputSchema": schema or {"type": "object", "properties": {}}, "risk": "read"}


class ExportToolsTest(unittest.TestCase):
    def test_dots_become_double_underscore(self):
        schemas, reverse = export_tools([tool("campus.search_courses")])
        self.assertEqual(schemas[0]["function"]["name"], "campus__search_courses")
        self.assertEqual(reverse["campus__search_courses"], "campus.search_courses")

    def test_schema_is_passed_through(self):
        schema = {"type": "object", "properties": {"k": {"type": "string"}}, "required": ["k"]}
        schemas, _ = export_tools([tool("a.b", schema=schema)])
        self.assertEqual(schemas[0]["function"]["parameters"], schema)

    def test_unrepresentable_name_raises(self):
        with self.assertRaises(ValueError):
            export_tools([tool("bad name!")])


class LoopTest(unittest.TestCase):
    def test_happy_path_records_every_call(self):
        gateway = StubGateway([tool("campus.search_courses")])
        plan = [("call", "campus__search_courses", {"keyword": "x"}), ("answer", "done")]
        report = run_loop(gateway, "student", "task", ScriptedModel(plan))
        self.assertEqual(report["tool_calls"], 1)
        self.assertEqual(report["answer"], "done")
        self.assertEqual(gateway.calls[0]["name"], "campus.search_courses")

    def test_approval_pauses_then_resumes(self):
        gateway = StubGateway([tool("campus.get_grades")],
                              fail_with={"campus.get_grades": ApprovalRequired(
                                  "campus.get_grades", "read", "{}")})
        seen = []
        plan = [("call", "campus__get_grades", {"student_id": "1"}), ("answer", "ok")]
        report = run_loop(gateway, "student", "task", ScriptedModel(plan),
                          on_approval=lambda exc: seen.append(exc.tool) or True)
        self.assertEqual(report["approvals"], 1)
        self.assertEqual(len(seen), 1)
        self.assertTrue(gateway.calls[-1]["approved"], "重试必须带 approved=True")

    def test_approval_denied_is_surfaced_not_fatal(self):
        gateway = StubGateway([tool("campus.get_grades")],
                              fail_with={"campus.get_grades": ApprovalRequired(
                                  "campus.get_grades", "read", "{}")})
        plan = [("call", "campus__get_grades", {"student_id": "1"}), ("answer", "ok")]
        report = run_loop(gateway, "student", "task", ScriptedModel(plan),
                          on_approval=lambda exc: False)
        self.assertEqual(report["denied"], 1)
        self.assertEqual(report["answer"], "ok")

    def test_permission_denied_is_recorded(self):
        gateway = StubGateway([tool("notice.publish_notice")],
                              fail_with={"notice.publish_notice": PermissionDenied("nope")})
        plan = [("call", "notice__publish_notice", {"title": "t"}), ("answer", "ok")]
        report = run_loop(gateway, "guest", "task", ScriptedModel(plan))
        self.assertEqual(report["denied"], 1)
        self.assertEqual(report["trace"][0]["outcome"], "denied")

    def test_budget_exceeded_counts_as_error(self):
        gateway = StubGateway([tool("campus.search_courses")],
                              fail_with={"campus.search_courses": BudgetExceeded("done")})
        plan = [("call", "campus__search_courses", {"keyword": "x"}), ("answer", "ok")]
        report = run_loop(gateway, "student", "task", ScriptedModel(plan))
        self.assertEqual(report["errors"], 1)

    def test_max_steps_caps_the_loop(self):
        gateway = StubGateway([tool("campus.search_courses")])
        plan = [("call", "campus__search_courses", {"keyword": str(i)}) for i in range(10)]
        report = run_loop(gateway, "student", "task", ScriptedModel(plan), max_steps=3)
        self.assertEqual(report["tool_calls"], 3)
        self.assertIsNone(report["answer"])

    def test_malformed_arguments_do_not_crash(self):
        class BadJSON(ScriptedModel):
            def chat(self, messages, tools):
                return {"content": None,
                        "tool_calls": [{"id": "c1", "name": "campus__search_courses",
                                        "arguments": "{not json"}],
                        "usage": {}}

        gateway = StubGateway([tool("campus.search_courses")])
        report = run_loop(gateway, "student", "task", BadJSON([]), max_steps=2)
        self.assertEqual(gateway.calls[0]["arguments"], {})

    def test_cost_estimate(self):
        cost = estimate_cost("deepseek-chat", {"prompt_tokens": 1_000_000,
                                              "completion_tokens": 0})
        self.assertAlmostEqual(cost, 0.27)
        self.assertIsNone(estimate_cost("unknown-model", {"prompt_tokens": 1,
                                                          "completion_tokens": 1}))


if __name__ == "__main__":
    unittest.main()
