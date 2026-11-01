"""Write MCP client configs that point at this repo's servers.

    python clients/install_clients.py --print        # 只打印，不落盘
    python clients/install_clients.py --claude       # 合并进 Claude Desktop
    python clients/install_clients.py --cursor       # 写 .cursor/mcp.json
    python clients/install_clients.py --all

Design notes
------------
* Every entry launches `mcp_launch.py <server>` with an absolute interpreter
  path. Clients spawn the server without a working directory, and our servers
  need the repo root on sys.path — the launcher fixes that.
* `--claude` **merges** into the existing file instead of overwriting it, so
  other people's servers survive. A config file is user data, not ours.
* Before writing, the target file is copied to `<name>.bak-<timestamp>`. Claude
  Desktop silently ignores a malformed config, so a bad edit costs the user a
  debugging session with zero feedback.
"""

import argparse
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "mcp_launch.py")
SERVERS = ["campus", "library", "notice", "github"]


def build_config():
    python = sys.executable
    return {
        "mcpServers": {
            name: {
                "command": python,
                "args": [LAUNCHER, name],
                "env": {},
            }
            for name in SERVERS
        }
    }


def claude_path():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return os.path.join(appdata, "Claude", "claude_desktop_config.json")


def cursor_path():
    # Project-scoped config: travels with the repo, good for a demo.
    return os.path.join(ROOT, ".cursor", "mcp.json")


def merge_into(path, new):
    """Merge new servers into an existing config, keeping unrelated keys."""
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                existing = json.load(handle)
        except (json.JSONDecodeError, OSError) as exc:
            raise SystemExit(
                f"{path} 不是合法 JSON（{exc}）。先修好它，或手动改；"
                f"我不覆盖别人的配置文件。"
            )
    servers = existing.setdefault("mcpServers", {})
    added = [name for name in new["mcpServers"] if name not in servers]
    servers.update(new["mcpServers"])
    return existing, added


def write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = f"{path}.bak-{stamp}"
        shutil.copy2(path, backup)
        print(f"  已备份 -> {backup}")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"  已写入 {path}")


def install(kind, config):
    if kind == "claude":
        path = claude_path()
        if not path:
            raise SystemExit("找不到 %APPDATA%，无法定位 Claude Desktop 配置")
        if not os.path.isdir(os.path.dirname(path)):
            print(f"  ! 目录不存在：{os.path.dirname(path)}")
            print("    Claude Desktop 可能还没安装。配置已创建，装上后即可生效。")
        payload, added = merge_into(path, config)
        write(path, payload)
        print(f"  新增 {len(added)} 个 server，其余保持不变")
        print("  完全退出 Claude Desktop（托盘也要退）再启动，才会加载新配置")
    else:
        path = cursor_path()
        payload, _ = merge_into(path, config)
        write(path, payload)
        print("  Cursor：打开项目后 Settings → MCP，确认四个 server 为绿点")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", dest="do_print", action="store_true")
    parser.add_argument("--claude", action="store_true")
    parser.add_argument("--cursor", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    config = build_config()
    if args.do_print or not (args.claude or args.cursor or args.all):
        print(json.dumps(config, ensure_ascii=False, indent=2))
        if not args.do_print:
            print("\n加 --claude 或 --cursor 才会写入。", file=sys.stderr)
        return 0

    if args.claude or args.all:
        print("Claude Desktop:")
        install("claude", config)
    if args.cursor or args.all:
        print("\nCursor:")
        install("cursor", config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
