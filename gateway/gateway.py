"""Tool gateway: aggregates several MCP servers behind one policy-enforcing surface.

The four things this adds on top of the protocol itself, and the four things a
production team actually needs:
  1. discovery + deterministic namespacing across servers
  2. risk-tiered authorization per principal
  3. human approval for write / sensitive tools (the HITL hook)
  4. audit trail, retry with restart, and result truncation
"""

import hashlib
import json
import os
import time
from datetime import datetime, timezone

from .client import RpcFailure, StdioClient, TransportError

RISK_ORDER = {"read": 0, "write": 1, "admin": 2}
MAX_RESULT_CHARS = 2000


class PermissionDenied(Exception):
    pass


class ApprovalRequired(Exception):
    def __init__(self, tool, risk, preview):
        self.tool = tool
        self.risk = risk
        self.preview = preview
        super().__init__(f"{tool} requires approval (risk={risk})")


class BudgetExceeded(Exception):
    pass


class Gateway:
    def __init__(self, servers, policies, risk_registry=None, cwd=".",
                 audit_path="gateway_audit.jsonl", max_calls_per_task=12,
                 max_result_chars=MAX_RESULT_CHARS):
        self.servers = servers          # {"campus": ["-m", "servers.campus_server"]}
        self.policies = policies
        self.risk_registry = risk_registry or {}
        self.cwd = cwd
        self.max_result_chars = max_result_chars
        self.discovery = {}
        self.audit_path = audit_path
        self.max_calls_per_task = max_calls_per_task
        self._clients = {}
        self._tools = {}                # "campus.search_courses" -> descriptor
        self._calls_in_task = 0

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        for alias, argv in self.servers.items():
            client = StdioClient(argv, cwd=self.cwd, name=alias)
            discover = client.request("server/discover", {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientInfo": {"name": "gateway", "version": "0.1.0"},
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            })
            versions = discover.get("supportedVersions", [])
            if "2026-07-28" not in versions:
                raise RuntimeError(f"{alias} does not support 2026-07-28: {versions}")
            self.discovery[alias] = discover
            listing = client.request("tools/list", {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientInfo": {"name": "gateway", "version": "0.1.0"},
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            })
            for tool in listing.get("tools", []):
                # Namespacing is mandatory: two servers may ship the same tool name.
                qualified = f"{alias}.{tool['name']}"
                self._tools[qualified] = {**tool, "_server": alias, "_local": tool["name"]}
            self._clients[alias] = client
        return self

    def stop(self):
        for client in self._clients.values():
            client.close()

    def reset_task(self):
        self._calls_in_task = 0

    # ---------------------------------------------------------------- discovery
    def list_tools(self, principal):
        visible = []
        for name, tool in sorted(self._tools.items()):
            allowed, _ = self._authorize(principal, tool)
            if allowed:
                visible.append({
                    "name": name,
                    "description": tool.get("description", ""),
                    "risk": self._risk(tool),
                    "inputSchema": tool.get("inputSchema", {}),
                })
        return visible

    def _risk(self, tool):
        # The spec explicitly says tool annotations are untrusted unless the
        # server is trusted. So the gateway keeps its own registry and treats
        # annotations only as a fallback hint.
        qualified = f"{tool['_server']}.{tool['_local']}"
        if qualified in self.risk_registry:
            return self.risk_registry[qualified]
        annotations = tool.get("annotations") or {}
        if annotations.get("readOnlyHint") is True:
            return "read"
        return "write"

    # ---------------------------------------------------------------- policy
    def _authorize(self, principal, tool):
        policy = self.policies.get(principal)
        if policy is None:
            return False, f"unknown principal: {principal}"

        qualified = f"{tool['_server']}.{tool['_local']}"
        if qualified in policy.get("deny", []):
            return False, "explicitly denied"

        risk = self._risk(tool)
        if RISK_ORDER[risk] > RISK_ORDER[policy["max_risk"]]:
            return False, f"risk={risk} exceeds principal ceiling={policy['max_risk']}"

        allow = policy.get("allow", [])
        if "*" in allow or qualified in allow:
            return True, "allowed"
        return False, "not in allowlist"

    def _needs_approval(self, tool):
        annotations = tool.get("annotations") or {}
        if annotations.get("readOnlyHint") is False:
            return True
        if annotations.get("sensitive"):
            return True
        return False

    # ---------------------------------------------------------------- execution
    def call(self, principal, qualified_name, arguments=None, approved=False, timeout=10.0):
        arguments = arguments or {}
        tool = self._tools.get(qualified_name)
        if tool is None:
            raise ValueError(f"unknown tool: {qualified_name}")

        allowed, reason = self._authorize(principal, tool)
        if not allowed:
            self._audit(principal, qualified_name, arguments, "denied", reason, 0, True)
            raise PermissionDenied(f"{qualified_name}: {reason}")

        if self._needs_approval(tool) and not approved:
            preview = json.dumps(arguments, ensure_ascii=False)[:200]
            self._audit(principal, qualified_name, arguments, "needs_approval", "", 0, False)
            raise ApprovalRequired(qualified_name, self._risk(tool), preview)

        if self._calls_in_task >= self.max_calls_per_task:
            self._audit(principal, qualified_name, arguments, "budget_exceeded", "", 0, True)
            raise BudgetExceeded(f"task call budget {self.max_calls_per_task} exhausted")

        self._calls_in_task += 1
        started = time.perf_counter()
        attempts = 0
        last_error = None
        while attempts < 2:
            attempts += 1
            try:
                result = self._call_once(tool, arguments, timeout)
                elapsed = int((time.perf_counter() - started) * 1000)
                self._audit(principal, qualified_name, arguments, "allowed", "", elapsed,
                            bool(result.get("isError")))
                return self._truncate(result)
            except TransportError as exc:
                last_error = exc
                self._restart(tool["_server"])
            except RpcFailure as exc:
                elapsed = int((time.perf_counter() - started) * 1000)
                self._audit(principal, qualified_name, arguments, "rpc_error", str(exc), elapsed, True)
                raise
        elapsed = int((time.perf_counter() - started) * 1000)
        self._audit(principal, qualified_name, arguments, "transport_error", str(last_error), elapsed, True)
        raise last_error

    def _call_once(self, tool, arguments, timeout):
        client = self._clients[tool["_server"]]
        return client.request("tools/call", {
            "name": tool["_local"],
            "arguments": arguments,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {"name": "gateway", "version": "0.1.0"},
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        }, timeout=timeout)

    def _restart(self, alias):
        try:
            self._clients[alias].close()
        except Exception:
            pass
        client = StdioClient(self.servers[alias], cwd=self.cwd, name=alias)
        self._clients[alias] = client

    def _truncate(self, result):
        """Truncate by *content block*, keeping head and tail. Dropping the tail
        silently is how agents end up confidently wrong about file contents."""
        changed = False
        blocks = []
        for block in result.get("content", []):
            if block.get("type") == "text" and len(block.get("text", "")) > self.max_result_chars:
                text = block["text"]
                half = self.max_result_chars // 2
                kept = len(text) - self.max_result_chars
                block = {**block, "text": f"{text[:half]}\n...[truncated {kept} chars]...\n{text[-half:]}"}
                changed = True
            blocks.append(block)
        return {**result, "content": blocks, "_truncated": changed}

    # ---------------------------------------------------------------- audit
    def _audit(self, principal, tool, arguments, decision, reason, elapsed_ms, is_error):
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "principal": principal,
            "tool": tool,
            "args_digest": hashlib.sha256(
                json.dumps(arguments, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()[:12],
            "decision": decision,
            "reason": reason,
            "latency_ms": elapsed_ms,
            "is_error": is_error,
        }
        with open(self.audit_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record
