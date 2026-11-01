# 接入真实 MCP 客户端

本地单测全绿不代表客户端能用。这个文件记录四条接入路径，以及真正接进去之后才暴露的两个 bug。

截图在 `docs/screenshots/`，全部是 MCP Inspector 2.13.0 连接本仓库 `campus` server 的实拍。

---

## 0. 先解决一个前置问题：客户端不给设工作目录

Claude Desktop 和 Cursor 的 MCP 配置只有 `command` / `args` / `env`，**没有 cwd**。而我们的 server 用绝对导入（`from datasource import campus_db`），只有在仓库根目录下才能启动。

常见做法和它们的问题：

| 做法 | 问题 |
|---|---|
| 每个 client 配置里手写 `PYTHONPATH` | 每加一个客户端就要复制一遍，打错字时客户端只报 "server disconnected"，没有任何提示 |
| 写 `.bat` 包装 | 多一层 cmd.exe，关闭时信号传不到 Python，进程会残留 |
| **`mcp_launch.py`** | 一个绝对路径入口，内部 `chdir` + `sys.path` + `runpy`，客户端只填 `python.exe <launcher> <server>` |

```bash
python mcp_launch.py campus    # 从任何目录都能跑
```

---

## 1. MCP Inspector（零账号，最快）

Claude Desktop 和 Cursor 都要登录账号，Inspector 不用，而且是 MCP 官方调试工具。面试时同样认——它证明的是"这个 server 符合规范，官方工具能发现并调用"。

```bash
npx -y @modelcontextprotocol/inspector \
    "D:/python/python.exe" "C:/Users/江00/WorkBuddy/2026-11-01-18-45-34/project-01-mcp-gateway/mcp_launch.py" "campus"
```

终端会打印带 token 的地址，浏览器打开后：

1. 打开 `python.exe` 右侧的开关 → **Connected**
2. 顶部切到 **Tools** → 列出 5 个工具
3. 点「检索课程」→ 填 `keyword=智能体` → **Execute Tool**

### 截图

**① 连接成功**

![连接成功](screenshots/01-connected.png)

注意三处：右上角绿点 `Connected`；协商版本 **MCP 2025-11-25**；右侧 Messages 面板标着 **LEGACY**，顺序是 `initialize OK` → `notifications/initialized` → `tools/list COMPLETE`。

这就是双时代设计在真实客户端上生效的证据：Inspector 是旧版客户端，走的是 `initialize` 握手，被我们的 legacy 回退接住了。如果只实现 2026-07-28 的无状态版本，这一步会直接失败。

**② 工具发现**

![工具列表](screenshots/02-tools.png)

`Tools (5)`，五个工具的名称和中文标题都由 server 的 `tools/list` 返回。

**③ 工具详情**

![工具详情](screenshots/03-tool-detail.png)

`READ-ONLY` 徽章不是 Inspector 猜的，是我们返回的 `annotations.readOnlyHint: true`，客户端读到了并渲染出来。参数表单（`keyword*` / `term` / `limit`）由 `inputSchema` 自动生成。

**④ 真实调用**

![调用结果](screenshots/04-call-result.png)

返回 CS401「智能体系统」，张一鸣，2 学分，容量 60 / 已选 41 / 余量 19，`TOOLS/CALL COMPLETE 81ms`。

下方还有一块 **Structured Output**——那是 `structuredContent`，说明客户端认得这个字段（这点后面有用，见坑 2）。

---

## 2. Claude Desktop

```bash
# 1. 安装（需要 Anthropic 账号，国内网络可能连不上）
winget install --id Anthropic.Claude -e

# 2. 写入配置（会自动备份原文件，只合并不覆盖）
python clients/install_clients.py --claude

# 3. 完全退出再启动（托盘图标也要退，否则不重载配置）
```

配置写到 `%APPDATA%\Claude\claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "campus": {
      "command": "D:\\python\\python.exe",
      "args": ["C:\\...\\project-01-mcp-gateway\\mcp_launch.py", "campus"]
    }
  }
}
```

启动后在对话框里输入：

> 用 campus 查一下智能体系统这门课还有多少余量

Claude 会弹出工具调用确认框，显示 `search_courses` 和参数——**那张确认框截图是简历里最有说服力的一张**，因为它同时证明了：工具被发现、参数被正确生成、写操作会征求用户同意。

### 实测结论（2026-11，本机）

安装与配置全部成功：0.14.10 安装包签名有效，配置写入 `%APPDATA%\Claude\claude_desktop_config.json`（注意数据目录在 Roaming，`%LOCALAPPDATA%\Claude` 下那个 0 字节的历史残留文件不是它读的），server 端裸探针验证正常。但启动后停在 **"App unavailable in region"**——Anthropic 对中国大陆的官方区域限制，请求根本到不了登录流程，MCP 也就不会加载。

两个教训值得记：

1. **"客户端装好了"和"客户端可用"之间隔着账号与地区政策**。MCP server 完全符合规范也没用，客户端主流程走不到 MCP 那一步。
2. 判断"是谁没连上"要看日志分层：main.log 里的 `Unexpected token '<', "<!DOCTYPE "...` 是后端返回了 HTML 错误页，不是我们的配置坏了。先分层定位，再动手改。

配置文件保留——将来在受支持地区登录后即可直接生效，四个 server 不需要重装。

---

## 3. Cursor

```bash
python clients/install_clients.py --cursor    # 写 .cursor/mcp.json（项目级）
```

打开项目 → `Settings → MCP` → 四个 server 应为绿点。然后在 Agent 模式下提问，Cursor 会自动路由到我们的工具。

项目级配置的好处是跟着仓库走，演示时不用现场改全局配置。

---

## 4. Cline（Claude Desktop 的替代路径）

Claude Desktop 被区域限制挡住后，工具调用确认框这张关键截图换 Cline 拿：它是 VS Code / Cursor 扩展（`saoudrizwan.claude-dev`），本身就是 MCP 原生客户端，支持任意 OpenAI 兼容端点——配 DeepSeek 正好复用已有的 API key，账号零门槛。

```bash
# 扩展（Cursor 或 VS Code 均可）
cursor --install-extension saoudrizwan.claude-dev

# 写入 MCP 配置（自动探测已装的宿主，VS Code / Cursor / VSCodium 都会写）
python clients/install_clients.py --cline
```

配置写到 `<宿主>\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`，比 `.cursor/mcp.json` 多三个字段：

```json
{
  "command": "C:\\...\\python.exe",
  "args": ["C:\\...\\mcp_launch.py", "campus"],
  "disabled": false,
  "autoApprove": [],
  "timeout": 60
}
```

`autoApprove` 刻意留空：每个工具调用都走 Cline 的审批对话框。这和网关"写操作必须过审批门"是同一个姿态——**客户端审批 UI 与服务端权限策略叠成双保险**，而且让确认框截图可复现，不是碰运气截到的一次性画面。

剩余手动步骤只有一步：Cline 的 provider 选择存在扩展的 globalState（SQLite）里，不能预写文件，需要在面板里选 DeepSeek、填一次 key。

---

## 5. 接入后才暴露的两个 bug

这两个都是**本地自研客户端测不出来**的：我们的 `gateway/client.py` 和真实客户端在编码、类型宽容度上的假设不一样。

### 坑 1：stdin 不是 UTF-8，中文全变成乱码（严重）

**现象**：Inspector 里点 Execute，只显示 `Tool Call Failed / Tool Error / Internal error`。同样的调用用命令行 `echo ... | python mcp_launch.py` 却是成功的。Console 面板里翻到真正的堆栈：

```
UnicodeEncodeError: 'utf-8' codec can't encode character '\udc93' in position 5:
surrogates not allowed
  File "datasource/campus_db.py", line 146, in search_courses
    rows = [dict(row) for row in conn.execute(sql, args)]
```

**根因**：`\udc93` 是**孤立代理字符**。Windows 上 `sys.stdin` 的默认编码是 ANSI 代码页（这台机器是 cp936），Node 客户端发来的是 UTF-8 字节，Python 用 cp936 + `surrogateescape` 解码，"智能体" 就变成了几个无法编码的代理字符。

`json.loads` 照收不误（JSON 字符串里允许转义），问题一路潜伏到 SQLite 执行阶段才炸，而此时错误信息里已经看不到"是哪个参数坏了"。**这就是它表现为 "Internal error" 而不是"参数编码错误"的原因。**

**修复**：三个流全部钉死 UTF-8，而不是只设 stdout。

```python
for stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
```

**教训**：只要协议里有中文，stdio 的三个流一个都不能漏。而且**编码错误会在下游很远处才显形**，堆栈指的位置（SQLite）和真正的原因（stdin 解码）差了三层。

### 坑 2：数字字段被客户端传成字符串

**现象**：`search_courses` 的 `limit` 字段，Inspector 的数字输入框提交的是 `"10"` 而不是 `10`，我们返回 `isError: true`、`Invalid arguments: $.limit: expected integer, got str`。

**根因**：JSON 表单控件普遍把值当字符串提交。严格校验对库边界是对的，但 server 是**网络边界**，得按 Postel 定律宽容一点。

**修复**：只在 schema 明确要求 `integer` / `number` / `boolean` 时才尝试转换，且只转明确可解析的值：

```python
arguments = self._coerce_scalars(tool.input_schema, arguments)   # 调用前
```

`limit="10"` → `10` 通过；`limit="abc"` 仍然被拒。类型安全没丢，只是不再因为一个引号把整个请求打死。

**教训**：这两类问题（编码、类型宽容度）都不会出现在自己的测试里，只会在别人的客户端里出现。**"接入真实客户端"这一步的价值不在于截图，在于它是一次真正的互操作测试。**

---

## 6. 面试怎么讲这一段

> 我自己写的客户端和 45 个单测都通过了，但接 MCP Inspector 时中文查询全部失败，界面只显示 Internal error。翻客户端 Console 才看到真正的堆栈是 UnicodeEncodeError——stdin 在 Windows 上按 cp936 解码，UTF-8 的中文字节变成孤立代理字符，`json.loads` 接受了，一路带到 SQLite 才报错。
>
> 修完之后我又发现数字参数被表单控件传成字符串，我们的严格校验把它拒了。这两件事让我意识到：**自研客户端和真实客户端的假设是不一样的**，本地测试通过不代表能接入生态。

这段话里没有任何夸大，但信息密度很高：定位过程（Console → 堆栈 → 代理字符）、根因（cp936 + surrogateescape）、修复（三流钉死 UTF-8）、反思（互操作测试）。**面试官听到"孤立代理字符"这个词，就知道你真的调过。**
