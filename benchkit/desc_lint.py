"""Static lint for tool descriptions — the guard rail behind the A/B result.

Why this file exists
--------------------
The selection benchmark moved deepseek-flash from 77.8% to 92.1% by rewriting
tool descriptions, with every original miss being the same shape: a *write*
tool replaced by its *read* sibling (submit_course_change -> search_courses).

That win is one careless edit away from disappearing. Someone tidying up a
string, or adding a 13th tool without a description, silently removes the very
signal the rewrite added. A benchmark only measures what was true the day it
ran; a lint keeps it true.

The three properties being enforced are the ones `benchkit/descriptions.py`
names as the reason the rewrites work:

  1. a 【只读】/【写入】 prefix, so read vs write is visible before the
     description is read to the end;
  2. a negative / exclusive constraint, because telling a model what a tool is
     NOT for is what separates it from its sibling;
  3. the user's own surface phrasing ("用户说「借一下」"), because the router
     matches surface forms rather than reasoning about ontology.

Severity
--------
ERROR   — a real regression that CI must block (missing prefix, empty or
          duplicate description, leftover placeholder).
WARNING — a genuine weakness that is knowingly accepted today. The rewrites
          were targeted at the 14 cases that actually failed, not written to
          make all 12 tools score perfectly, so a few write tools still lack
          an explicit "don't use the read sibling instead" clause. Reported so
          the debt stays visible, but not fatal — that is the `warning
          ratchet`: new warnings are easy to spot, and burning them down is
          an explicit choice rather than a surprise.

Two arms
--------
    python -m benchkit.desc_lint --arm improved    # CI runs this; must be 0 errors
    python -m benchkit.desc_lint --arm original    # the control group; expected to fail

Running the original arm is how you show *why* the rewrite mattered: same
rules, same tools, 12 errors instead of 0.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from benchkit import descriptions  # noqa: E402

# Server package -> module. Keep in sync with demo.py's SERVERS map.
SERVER_MODULES = {
    "campus": "servers.campus_server",
    "library": "servers.library_server",
    "notice": "servers.notice_server",
    "github": "servers.github_server",
}

READ_PREFIX = "【只读"
WRITE_PREFIX = "【写入"

MIN_CHARS = 15
PLACEHOLDERS = ("TODO", "FIXME", "XXX", "待补", "placeholder")
TRIGGER_RE = re.compile(r"用户(说|问|要)")
# A write tool must say what it is for *instead of* its read sibling.
EXCLUSIVE_MARKERS = ("不要", "即使", "应选本工具", "别用", "而不是先")

ERROR = "ERROR"
WARNING = "WARN"


class Finding:
    __slots__ = ("level", "code", "tool", "message")

    def __init__(self, level, code, tool, message):
        self.level = level
        self.code = code
        self.tool = tool
        self.message = message

    def as_dict(self):
        return {"level": self.level, "code": self.code,
                "tool": self.tool, "message": self.message}


def load_tools(arm="improved"):
    """Read the 12 registered tools, optionally swapping in rewritten text.

    `risk` always comes from the server definition (the gateway never trusts a
    description to decide risk, so neither does the lint).
    """
    tools = []
    for domain, module_name in SERVER_MODULES.items():
        module = importlib.import_module(module_name)
        server = module.server
        for name, tool in server.list_tools().items():
            qualified = f"{domain}.{name}"
            text = tool.description
            if arm == "improved":
                text = descriptions.IMPROVED.get(qualified, text)
            tools.append({
                "name": qualified,
                "description": text or "",
                "risk": tool.risk,
                "domain": domain,
            })
    return tools


def lint(tools):
    findings = []

    def add(level, code, tool, message):
        findings.append(Finding(level, code, tool, message))

    for tool in tools:
        text = tool["description"].strip()
        name = tool["name"]
        is_write = tool["risk"] in ("write", "admin")

        # --- structure -----------------------------------------------------
        if not text:
            add(ERROR, "E1-empty", name, "description is empty")
            continue
        if len(text) < MIN_CHARS:
            add(ERROR, "E2-short", name,
                f"description is {len(text)} chars, need >= {MIN_CHARS}")

        if is_write:
            if not text.startswith(WRITE_PREFIX):
                add(ERROR, "E3-prefix", name,
                    "write tool must start with 【写入, so the side effect is "
                    "visible before the text is read to the end")
        else:
            if not text.startswith(READ_PREFIX):
                add(ERROR, "E4-prefix", name,
                    "read tool must start with 【只读")

        for token in PLACEHOLDERS:
            if token in text:
                add(ERROR, "E5-placeholder", name,
                    f"leftover placeholder {token!r}")

        # --- surface trigger ----------------------------------------------
        if not TRIGGER_RE.search(text):
            add(ERROR, "E6-trigger", name,
                "no user phrasing (用户说/问/要); the router matches surface "
                "forms, not intent")

        # --- write-side risks ---------------------------------------------
        if is_write:
            if not any(marker in text for marker in EXCLUSIVE_MARKERS):
                add(WARNING, "W1-no-exclusion", name,
                    "write tool never says what NOT to do; a read sibling can "
                    "absorb this intent")
            bracket = text[text.find(WRITE_PREFIX) + len(WRITE_PREFIX):]
            bracket = bracket.split("】")[0]
            if len(bracket.strip("· ")) < 2:
                add(WARNING, "W2-no-impact", name,
                    "【写入】 carries no impact note (e.g. 库存减一 / 全校可见)")

    # --- cross-tool ---------------------------------------------------------
    by_domain = defaultdict(list)
    for tool in tools:
        by_domain[tool["domain"]].append(tool)

    for domain, group in by_domain.items():
        seen = {}
        for tool in group:
            key = tool["description"].strip()
            if key and key in seen:
                add(ERROR, "E7-duplicate", tool["name"],
                    f"identical description to {seen[key]}")
            seen[key] = tool["name"]

        writes = [t for t in group if t["risk"] in ("write", "admin")]
        reads = [t for t in group if t["risk"] == "read"]
        if not writes or not reads:
            continue
        for reader in reads:
            if not any(w["name"].split(".", 1)[1] in reader["description"] for w in writes):
                add(WARNING, "W3-no-pointer", reader["name"],
                    "read tool never names its write sibling ("
                    + ", ".join(w["name"].split(".", 1)[1] for w in writes)
                    + ")")

    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="desc_lint",
        description="Lint MCP tool descriptions for the properties the A/B "
                    "benchmark proved matter.",
    )
    parser.add_argument("--arm", choices=("improved", "original"), default="improved",
                        help="improved = the rewrite set (CI target); "
                             "original = the control group")
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings as failures too")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    tools = load_tools(args.arm)
    findings = lint(tools)
    errors = [f for f in findings if f.level is ERROR]
    warnings = [f for f in findings if f.level is WARNING]

    if args.json:
        print(json.dumps({
            "arm": args.arm,
            "tools": len(tools),
            "errors": len(errors),
            "warnings": len(warnings),
            "findings": [f.as_dict() for f in findings],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"desc-lint  arm={args.arm}  tools={len(tools)}")
        print("-" * 68)
        for finding in findings:
            print(f"  {finding.level:<5} {finding.code:<16} {finding.tool}")
            print(f"        {finding.message}")
        if not findings:
            print("  (clean)")
        print("-" * 68)
        print(f"{len(tools)} tools, {len(errors)} errors, {len(warnings)} warnings")

    if errors:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
