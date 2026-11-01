"""Protocol + policy tests. Stdlib unittest only, so it runs anywhere.

    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gateway.gateway import Gateway  # noqa: E402
from mcp_min.validate import validate  # noqa: E402
from servers import campus_server  # noqa: E402

META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
SERVER = campus_server.server


def call(method, params):
    return SERVER.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


class ModernEraTests(unittest.TestCase):
    def test_discover_lists_versions_and_identity(self):
        result = call("server/discover", {"_meta": META})["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertIn("2026-07-28", result["supportedVersions"])
        self.assertEqual(
            result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "campus-server"
        )

    def test_tools_list_is_deterministic_and_complete(self):
        first = call("tools/list", {"_meta": META})["result"]
        second = call("tools/list", {"_meta": META})["result"]
        names = [tool["name"] for tool in first["tools"]]
        self.assertEqual(names, sorted(names))
        self.assertEqual(names, [tool["name"] for tool in second["tools"]])
        self.assertEqual(len(names), 5)

    def test_missing_meta_is_rejected(self):
        response = call("tools/list", {})
        self.assertEqual(response["error"]["code"], -32602)

    def test_unsupported_version_returns_supported_list(self):
        response = call("tools/list", {"_meta": {**META, "io.modelcontextprotocol/protocolVersion": "1999-01-01"}})
        self.assertEqual(response["error"]["code"], -32022)
        self.assertIn("2026-07-28", response["error"]["data"]["supported"])

    def test_unknown_tool_is_protocol_error(self):
        response = call("tools/call", {"_meta": META, "name": "nope", "arguments": {}})
        self.assertEqual(response["error"]["code"], -32602)

    def test_bad_arguments_return_is_error_not_crash(self):
        response = call("tools/call", {"_meta": META, "name": "search_courses", "arguments": {}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("required", response["result"]["content"][0]["text"])

    def test_business_failure_returns_is_error(self):
        response = call("tools/call", {"_meta": META, "name": "get_grades",
                                       "arguments": {"student_id": "0000"}})
        self.assertTrue(response["result"]["isError"])

    def test_unknown_method_is_method_not_found(self):
        response = call("no/such/method", {"_meta": META})
        self.assertEqual(response["error"]["code"], -32601)


class LegacyEraTests(unittest.TestCase):
    def test_initialize_then_meta_less_calls_work(self):
        fresh = type(SERVER)(name="legacy-test")
        fresh._tools = SERVER._tools
        init = fresh.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-11-25"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-11-25")
        listing = fresh.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual(len(listing["result"]["tools"]), 5)

    def test_legacy_rejects_modern_version(self):
        fresh = type(SERVER)(name="legacy-test")
        response = fresh.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                 "params": {"protocolVersion": "2026-07-28"}})
        self.assertEqual(response["error"]["code"], -32022)


class ValidationTests(unittest.TestCase):
    def test_required_and_extra_fields(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}},
                  "required": ["a"], "additionalProperties": False}
        self.assertEqual(validate(schema, {"a": "x"}), [])
        self.assertTrue(validate(schema, {}))
        self.assertTrue(validate(schema, {"a": "x", "b": 1}))

    def test_enum_and_minimum(self):
        schema = {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["enroll", "drop"]},
            "limit": {"type": "integer", "minimum": 1}}}
        self.assertTrue(validate(schema, {"action": "transfer"}))
        self.assertTrue(validate(schema, {"limit": 0}))
        self.assertEqual(validate(schema, {"action": "drop", "limit": 3}), [])


class GatewayPolicyTests(unittest.TestCase):
    def make(self):
        return Gateway(servers={}, policies={
            "guest": {"max_risk": "read", "allow": ["a.read_tool"]},
            "admin": {"max_risk": "admin", "allow": ["*"]},
        }, risk_registry={"b.publish": "admin"})

    def test_allowlist_blocks_unlisted_tool(self):
        gateway = self.make()
        allowed, reason = gateway._authorize("guest", {
            "_server": "a", "_local": "other_tool", "annotations": {"readOnlyHint": True}})
        self.assertFalse(allowed)
        self.assertEqual(reason, "not in allowlist")

    def test_risk_registry_overrides_untrusted_annotations(self):
        gateway = self.make()
        allowed, reason = gateway._authorize("guest", {
            "_server": "b", "_local": "publish", "annotations": {"readOnlyHint": True}})
        self.assertFalse(allowed)
        self.assertIn("risk=admin", reason)

    def test_read_only_tool_passes(self):
        gateway = self.make()
        allowed, _ = gateway._authorize("guest", {
            "_server": "a", "_local": "read_tool", "annotations": {"readOnlyHint": True}})
        self.assertTrue(allowed)

    def test_truncation_keeps_head_and_tail(self):
        gateway = self.make()
        gateway.max_result_chars = 100
        result = gateway._truncate({"content": [{"type": "text", "text": "H" * 80 + "T" * 80}]})
        self.assertTrue(result["_truncated"])
        text = result["content"][0]["text"]
        self.assertIn("truncated", text)
        self.assertTrue(text.startswith("H"))
        self.assertTrue(text.endswith("T"))


class InteropRegressionTests(unittest.TestCase):
    """Bugs that only surfaced once a third-party client connected.

    Both were invisible to our own gateway client, which sends ints as ints and
    pins UTF-8 itself. They are worth a test each so they cannot come back.
    """

    def test_numeric_string_is_coerced(self):
        # The Inspector's number input submits "10", not 10.
        response = call("tools/call", {"_meta": META, "name": "search_courses",
                                       "arguments": {"keyword": "课", "limit": "5"}})
        result = response["result"]
        self.assertFalse(result.get("isError"), result.get("content"))

    def test_non_numeric_string_is_still_rejected(self):
        # Coercion must not turn into "accept anything".
        response = call("tools/call", {"_meta": META, "name": "search_courses",
                                       "arguments": {"keyword": "课", "limit": "abc"}})
        self.assertTrue(response["result"].get("isError"))

    def test_boolean_string_is_coerced(self):
        response = call("tools/call", {
            "_meta": META, "name": "submit_course_change",
            "arguments": {"student_id": "2021001", "course_id": "CS301",
                          "action": "drop", "confirm": "true"}})
        # Either it runs or it refuses for business reasons — but it must not
        # be rejected as "confirm: expected boolean, got str".
        text = response["result"]["content"][0]["text"]
        self.assertNotIn("expected boolean", text)

    @unittest.skipUnless(os.name == "nt", "cp936 stdin is a Windows-only failure mode")
    def test_utf8_payload_survives_narrow_stdin(self):
        """Force cp936 as the locale encoding and send UTF-8 Chinese anyway.

        Before the fix this raised UnicodeEncodeError deep inside SQLite and the
        client only saw "Internal error"; see clients/SETUP-CLIENTS.md.
        """
        import json as _json
        import subprocess

        launcher = os.path.join(ROOT, "mcp_launch.py")
        lines = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "search_courses",
                        "arguments": {"keyword": "智能体", "limit": "5"}}},
        ]
        payload = "\n".join(_json.dumps(x, ensure_ascii=False) for x in lines) + "\n"
        env = dict(os.environ, PYTHONIOENCODING="cp936")
        proc = subprocess.run(
            [sys.executable, launcher, "campus"],
            input=payload.encode("utf-8"),
            capture_output=True, timeout=60, env=env,
        )
        stdout = proc.stdout.decode("utf-8", "replace")
        last = [ln for ln in stdout.splitlines() if '"id":2' in ln]
        self.assertTrue(last, f"no response; stderr={proc.stderr.decode('utf-8', 'replace')[:400]}")
        self.assertIn("CS401", last[-1])
        self.assertNotIn("UnicodeEncodeError", proc.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
