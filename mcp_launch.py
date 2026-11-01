"""Launch a bundled MCP server with the right working directory.

Why this file exists
--------------------
Claude Desktop and Cursor spawn a server with a command + args and **no way to
set the working directory**. Our servers use absolute imports (`from datasource
import campus_db`), so they only work when the project root is on sys.path — and
`python -m servers.campus_server` also needs the root as cwd.

The usual workarounds are worse:
  - hard-coding PYTHONPATH in each client config means every new client needs
    the same hand-edited env block, and a typo produces a silent "server
    disconnected" with no error in the UI;
  - a .bat shim adds a cmd.exe layer that eats signals on shutdown.

So: one launcher, absolute path, chdir + sys.path fixed up, then runpy the module.
Clients only need `python.exe <this file> <server-name>`.

    python mcp_launch.py campus
    python mcp_launch.py library
    python mcp_launch.py notice
    python mcp_launch.py github
"""

import os
import runpy
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVERS = ("campus", "library", "notice", "github")


class _TeeStdin:
    """Yield stdin lines while appending a copy to a file.

    Needed because a client that misbehaves gives you nothing to debug with:
    the Inspector shows "Internal error" and hides the request body inside a
    Monaco editor you cannot read from the DOM. Set MCP_TRACE=<path> and the
    exact wire traffic lands on disk.
    """

    def __init__(self, stream, path):
        self._stream = stream
        self._log = open(path, "a", encoding="utf-8")

    def __iter__(self):
        for line in self._stream:
            try:
                self._log.write(line)
                self._log.flush()
            except (OSError, ValueError):
                pass
            yield line

    def __getattr__(self, item):
        return getattr(self._stream, item)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.stderr.write(f"usage: mcp_launch.py <{'|'.join(SERVERS)}>\n")
        return 2
    name = sys.argv[1]
    if name not in SERVERS:
        sys.stderr.write(f"unknown server {name!r}, expected one of {SERVERS}\n")
        return 2

    os.chdir(ROOT)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    # Pin all three streams to UTF-8 before wrapping stdin. On Windows the
    # default is cp936 and a UTF-8 payload decodes into lone surrogates, which
    # only blows up later inside SQLite as an opaque "Internal error".
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    trace = os.environ.get("MCP_TRACE")
    if trace:
        sys.stdin = _TeeStdin(sys.stdin, trace)

    # stdout belongs to the protocol. Anything printed by imports would corrupt
    # the newline-delimited JSON stream, so send it to stderr.
    runpy.run_module(f"servers.{name}_server", run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
