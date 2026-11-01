"""Data sources behind the MCP servers.

`campus_db` is a local SQLite store (swap for a real school API in production).
`github_api` talks to a real remote service and is where caching, rate limiting
and graceful degradation live.
"""

from . import campus_db, github_api  # noqa: F401
