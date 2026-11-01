"""MCP wire-format primitives, written by hand on purpose.

Targets protocol revision 2026-07-28 ("modern" era: stateless, per-request
_meta) while remaining able to answer legacy clients that still open with the
`initialize` handshake.
"""

import json

JSONRPC = "2.0"

MODERN_VERSION = "2026-07-28"
LEGACY_VERSION = "2025-11-25"
SUPPORTED_VERSIONS = [MODERN_VERSION, LEGACY_VERSION]

# JSON-RPC 2.0 standard codes
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603

# MCP-reserved range -32020..-32099
ERR_UNSUPPORTED_PROTOCOL_VERSION = -32022

# _meta keys reserved by the specification
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"


class RpcError(Exception):
    """Protocol-level error: surfaced as a JSON-RPC `error` object."""

    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_payload(self):
        payload = {"code": self.code, "message": self.message}
        if self.data is not None:
            payload["data"] = self.data
        return payload


class ToolError(Exception):
    """Business-level failure: surfaced as result.isError = True so the model
    can read the message and correct itself instead of crashing the loop."""


def dumps(message):
    """Newline-delimited framing: one message per line, never embedded newlines."""
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def ok(request_id, result):
    return {"jsonrpc": JSONRPC, "id": request_id, "result": result}


def fail(request_id, error):
    return {"jsonrpc": JSONRPC, "id": request_id, "error": error.to_payload()}


def unsupported_version(requested, supported=None):
    return RpcError(
        ERR_UNSUPPORTED_PROTOCOL_VERSION,
        "Unsupported protocol version",
        {"supported": supported or SUPPORTED_VERSIONS, "requested": requested},
    )
