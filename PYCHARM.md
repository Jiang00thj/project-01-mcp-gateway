# PyCharm 环境说明（已由助手配置好）

## 一键打开

双击项目根目录的 `open-project.bat`，或桌面上的 `Open-Project-01-PyCharm.bat`。

> 脚本必须是**纯 ASCII + CRLF**。之前两个中文名的 .bat 已废弃删除：UTF-8 中文路径在 GBK(936) 的 cmd 里会变乱码，反斜杠也可能被工具链改写成 `//`，导致 `start` 找不到 exe、双击只闪一下窗口。

没反应就用开始菜单：`Win` → 输入 `PyCharm` → 打开 → `Open` → 粘贴
`C:\Users\江00\WorkBuddy\2026-11-01-18-45-34\project-01-mcp-gateway`

## 已写入的配置

| 位置 | 内容 |
|---|---|
| `%APPDATA%\JetBrains\PyCharmCE2025.2\options\jdk.table.xml` | 注册解释器 `Python 3.14 (D:\python\python.exe)` |
| `.idea/misc.xml` | 项目 SDK 指向上面那个解释器 |
| `.idea/project-01-mcp-gateway.iml` | 模块类型 `PYTHON_MODULE`，源码根 = 项目根 |
| `.idea/encodings.xml` | 项目编码 UTF-8（避免中文输出乱码） |
| `.idea/runConfigurations/*.xml` | 3 个运行配置，`PYTHONUNBUFFERED=1` + `PYTHONIOENCODING=utf-8` |
| `.idea/.gitignore` | 忽略 `workspace.xml`，保留运行配置（可提交） |

## 三个运行配置（右上角下拉框）

1. **1 · demo.py（五场景演示）** → 期望末行 `全部断言通过。`
2. **2 · 全部单测（unittest）** → 期望 `Ran 16 tests ... OK`
3. **3 · 红队测试（bench injection）** → 期望 `拦截率：100.0% (12/12)`

都是 `D:\python\python.exe` 直接跑，项目零第三方依赖，不需要 `pip install`。

## 如果显示「Invalid interpreter」

按这 4 步改，10 秒：

`Settings`（Ctrl+Alt+S）→ `Project: project-01-mcp-gateway` → `Python Interpreter`
→ 齿轮 / `Add Interpreter` → `Add Local Interpreter` → `System Interpreter`
→ 路径填 `D:\python\python.exe` → `OK`

## 调试入口

- 在 `gateway/gateway.py` 的 `call()` 里打断点，跑配置 1，能直接看到每一次鉴权、截断、预算检查的入参。
- `mcp_min/server.py` 的 `handle()` 是协议分发入口，读它就懂 2026-07-28 版的无状态请求怎么处理。
- 想按单个测试调试：在 `tests/test_protocol.py` 里点函数左边的绿色三角。

## 版本

PyCharm Community 2025.2.6.1（免费，无需激活）· Python 3.14.5 (`D:\python\python.exe`)
