"""Minimal MCP client: drives a server subprocess over newline-delimited JSON-RPC.

This is the piece most tutorials hide behind the official SDK. Writing it by
hand makes the three things interviewers ask about explicit: framing, request
correlation, and what happens when the child process dies.
"""

import json
import os
import subprocess
import sys
import threading


class TransportError(RuntimeError):
    pass


class StdioClient:
    def __init__(self, args, cwd=None, name="server"):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        self.name = name
        self.proc = subprocess.Popen(
            [sys.executable, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=cwd,
            env=env,
        )
        self._next_id = 0
        self._pending = {}
        self._lock = threading.Lock()
        self.stderr_lines = []
        self._alive = True
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _read_stdout(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = message.get("id")
            with self._lock:
                slot = self._pending.pop(request_id, None)
            if slot is not None:
                slot["response"] = message
                slot["event"].set()
        self._alive = False
        with self._lock:
            waiting = list(self._pending.values())
            self._pending.clear()
        for slot in waiting:
            slot["event"].set()

    def _drain_stderr(self):
        for line in self.proc.stderr:
            self.stderr_lines.append(line.rstrip())
            if len(self.stderr_lines) > 200:
                del self.stderr_lines[0]

    def request(self, method, params, timeout=10.0):
        if not self._alive:
            raise TransportError(f"{self.name}: process is not running")
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            slot = {"event": threading.Event(), "response": None}
            self._pending[request_id] = slot
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise TransportError(f"{self.name}: write failed ({exc})") from exc

        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise TransportError(f"{self.name}: timeout after {timeout}s on {method}")

        response = slot["response"]
        if response is None:
            raise TransportError(f"{self.name}: no response (process exited?)")
        if "error" in response:
            raise RpcFailure(response["error"])
        return response.get("result", {})

    def notify(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        try:
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=3)
        except Exception:
            self.proc.kill()


class RpcFailure(RuntimeError):
    def __init__(self, error):
        self.code = error.get("code")
        self.data = error.get("data")
        super().__init__(error.get("message", "rpc error"))
