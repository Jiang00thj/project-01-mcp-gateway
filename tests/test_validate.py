"""Schema validation tests, focused on the constraints the gateway relies on.

`pattern` matters in practice: it is what stops a model from passing
`student_id="1 OR 1=1"` into a tool that then does its own string formatting.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_min.validate import validate  # noqa: E402


class PatternTest(unittest.TestCase):
    def test_valid_value_passes(self):
        self.assertEqual(validate({"type": "string", "pattern": r"^\d{7,12}$"}, "2021001"), [])

    def test_invalid_value_is_reported(self):
        errors = validate({"type": "string", "pattern": r"^\d{7,12}$"}, "2021001 OR 1=1")
        self.assertEqual(len(errors), 1)
        self.assertIn("does not match pattern", errors[0])

    def test_nested_pattern_inside_object(self):
        schema = {
            "type": "object",
            "properties": {"student_id": {"type": "string", "pattern": r"^\d{7,12}$"}},
            "required": ["student_id"],
            "additionalProperties": False,
        }
        self.assertEqual(validate(schema, {"student_id": "2021001"}), [])
        self.assertTrue(validate(schema, {"student_id": "abc"}))
        self.assertTrue(validate(schema, {"student_id": "2021001", "extra": 1}))

    def test_repo_pattern(self):
        schema = {"type": "string", "pattern": r"^[\w.-]+/[\w.-]+$"}
        self.assertEqual(validate(schema, "modelcontextprotocol/servers"), [])
        self.assertTrue(validate(schema, "not-a-repo"))

    def test_bounds_still_work(self):
        self.assertTrue(validate({"type": "integer", "minimum": 1, "maximum": 3}, 0))
        self.assertTrue(validate({"type": "integer", "minimum": 1, "maximum": 3}, 4))
        self.assertEqual(validate({"type": "integer", "minimum": 1, "maximum": 3}, 2), [])


if __name__ == "__main__":
    unittest.main()
