"""A dual-era MCP server core, ~200 lines, zero third-party dependencies.

Modern era (>= 2026-07-28): stateless. No initialize handshake. Every request
carries its protocol version and client capabilities in _meta.
Legacy era (<= 2025-11-25): the client opens with `initialize` and this object
keeps that choice for the lifetime of the process.
"""

import json
import sys
import traceback

from . import protocol as p
from .validate import validate


class Tool:
    def __init__(self, name, handler, title, description, input_schema,
                 output_schema=None, risk="read", annotations=None):
        self.name = name
        self.handler = handler
        self.title = title
        self.description = description
        self.input_schema = input_schema
        self.output_schema = output_schema
        self.risk = risk
        self.annotations = annotations or {}

    def descriptor(self):
        item = {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": self.annotations,
        }
        if self.output_schema:
            item["outputSchema"] = self.output_schema
        return item


class MCPServer:
    def __init__(self, name, version="1.0.0", instructions=""):
        self.name = name
        self.version = version
        self.instructions = instructions
        self._tools = {}
        self._legacy_version = None  # set once a legacy client handshakes

    # ---------------------------------------------------------------- registry
    def tool(self, name=None, title="", description="", input_schema=None,
             output_schema=None, risk="read", annotations=None):
        def decorator(func):
            tool_name = name or func.__name__
            self._tools[tool_name] = Tool(
                tool_name, func, title or tool_name, description or (func.__doc__ or "").strip(),
                input_schema or {"type": "object", "properties": {}},
                output_schema, risk, annotations,
            )
            return func
        return decorator

    # ---------------------------------------------------------------- dispatch
    def handle(self, message):
        """Return a response dict, or None for notifications / client replies."""
        if not isinstance(message, dict):
            return p.fail(None, p.RpcError(p.ERR_INVALID_REQUEST, "message must be an object"))

        request_id = message.get("id")
        method = message.get("method")

        if method is None:
            # A response or garbage from the client. Servers never receive
            # responses on stdio; stay silent rather than emitting noise.
            return None

        is_notification = request_id is None
        params = message.get("params") or {}

        try:
            result = self._route(method, params)
        except p.RpcError as exc:
            return None if is_notification else p.fail(request_id, exc)
        except p.ToolError as exc:
            raise
        except Exception:
            traceback.print_exc(file=sys.stderr)
            error = p.RpcError(p.ERR_INTERNAL, "Internal error")
            return None if is_notification else p.fail(request_id, error)

        if is_notification:
            return None
        return p.ok(request_id, result)

    def _route(self, method, params):
        if method == "initialize":
            return self._initialize(params)
        if method == "notifications/initialized":
            return {}
        if method == "ping":
            return {}

        self._check_version(params)

        if method == "server/discover":
            return self._discover()
        if method == "tools/list":
            return self._list_tools()
        if method == "tools/call":
            return self._call_tool(params)

        raise p.RpcError(p.ERR_METHOD_NOT_FOUND, f"Method not found: {method}")

    # ---------------------------------------------------------------- era
    def _check_version(self, params):
        meta = params.get("_meta") or {}
        version = meta.get(p.META_VERSION)

        if version is None:
            if self._legacy_version:
                return  # legacy session already established
            raise p.RpcError(
                p.ERR_INVALID_PARAMS,
                f"Missing _meta['{p.META_VERSION}']: this server speaks 2026-07-28 "
                f"and no legacy initialize handshake was performed",
            )

        if version not in p.SUPPORTED_VERSIONS:
            raise p.unsupported_version(version)
        if version != p.MODERN_VERSION and not self._legacy_version:
            raise p.unsupported_version(version)

    def _initialize(self, params):
        requested = params.get("protocolVersion")
        supported_legacy = [v for v in p.SUPPORTED_VERSIONS if v != p.MODERN_VERSION]
        if requested not in supported_legacy:
            raise p.unsupported_version(requested, supported_legacy)
        self._legacy_version = requested
        return {
            "protocolVersion": requested,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.name, "version": self.version},
            "instructions": self.instructions,
        }

    # ---------------------------------------------------------------- methods
    def _discover(self):
        return {
            "resultType": "complete",
            "supportedVersions": p.SUPPORTED_VERSIONS,
            "capabilities": {"tools": {"listChanged": False}},
            "_meta": {p.META_SERVER_INFO: {"name": self.name, "version": self.version}},
            "instructions": self.instructions,
            "ttlMs": 3600000,
            "cacheScope": "public",
        }

    def _list_tools(self):
        tools = [tool.descriptor() for tool in self._tools.values()]
        tools.sort(key=lambda item: item["name"])  # deterministic order
        return {
            "resultType": "complete",
            "tools": tools,
            "ttlMs": 60000,
            "cacheScope": "public",
        }

    def _call_tool(self, params):
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = self._tools.get(name)
        if tool is None:
            raise p.RpcError(p.ERR_INVALID_PARAMS, f"Unknown tool: {name}")

        errors = validate(tool.input_schema, arguments)
        if errors:
            # Model-correctable: return isError so the loop can retry with
            # fixed arguments instead of aborting the whole task.
            return self._tool_error("Invalid arguments: " + "; ".join(errors))

        try:
            structured = tool.handler(**arguments)
        except p.ToolError as exc:
            return self._tool_error(str(exc))
        except TypeError as exc:
            return self._tool_error(f"Bad arguments for {name}: {exc}")

        text = structured if isinstance(structured, str) else json.dumps(structured, ensure_ascii=False)
        result = {
            "resultType": "complete",
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }
        if tool.output_schema and not isinstance(structured, str):
            result["structuredContent"] = structured
        return result

    @staticmethod
    def _tool_error(message):
        return {
            "resultType": "complete",
            "content": [{"type": "text", "text": message}],
            "isError": True,
        }

    # ---------------------------------------------------------------- transport
    def serve_stdio(self):
        # stdout is the wire. Anything else printed there corrupts the stream,
        # so every log line goes to stderr.
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                response = p.fail(None, p.RpcError(p.ERR_PARSE, f"Parse error: {exc}"))
            else:
                response = self.handle(message)
            if response is not None:
                sys.stdout.write(p.dumps(response) + "\n")
                sys.stdout.flush()
