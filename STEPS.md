# 项目一 · 具体操作步骤

从"仓库里有个骨架"到"简历上有一条能扛住追问的项目"，一共 27 步。
每步都给了**命令**和**预期输出**，对不上就停下来排查，别往下走。

> 前置：Python 3.10+。仓库零第三方依赖，只有接入真实客户端和跑真模型基准时才需要装包。

---

## 阶段 0 · 环境（3 步，约 20 分钟）

### 步骤 1 · 确认 Python 版本

```bash
python --version
```

**预期**：`Python 3.10.x` 或更高。低于 3.10 会因为我用的 `dict` 合并语法报错。

### 步骤 2 · 进目录

```bash
cd "C:/Users/江00/WorkBuddy/2026-11-01-18-45-34/project-01-mcp-gateway"
```

### 步骤 3 · 建虚拟环境（可选但建议）

```bash
python -m venv .venv
.venv/Scripts/activate
```

**预期**：命令行前面出现 `(.venv)`。以后所有命令都在这个环境里跑。

---

## 阶段 1 · 先跑通现有骨架（4 步，约 30 分钟）

这一步的目的不是学东西，是**建立"我知道它现在是什么状态"的基线**。

### 步骤 4 · 跑单测

```bash
python -m unittest discover -s tests -v
```

**预期**：`Ran 16 tests ... OK`

如果失败，先看是哪个类：`ModernEraTests` 挂了是协议层问题，`GatewayPolicyTests` 挂了是策略问题。**不要**在测试红的情况下继续。

### 步骤 5 · 跑端到端演示

```bash
python demo.py
```

**预期关键行**：

```
campus   campus-server    版本=['2026-07-28', '2025-11-25'] 工具=5
guest 越权被拒：notice.publish_notice: risk=admin exceeds principal ceiling=read
拦截：campus.get_grades requires approval (risk=read)
第 13 次调用被拦：task call budget 12 exhausted
全部断言通过。
```

### 步骤 6 · 跑红队

```bash
python bench.py injection
```

**预期**：12 行 `PASS`，最后 `拦截率：100.0%  (12/12)`。

### 步骤 7 · 肉眼验证一次协议

手动喂一条消息给 Server，看它原样回什么：

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28"}}}' | python -m servers.campus_server
```

**预期**：一行 JSON，含 `"resultType":"complete"`、`"supportedVersions":["2026-07-28","2025-11-25"]`。

> **这一步的价值**：面试官问"你怎么调试 MCP"，你答"直接 echo 一行 JSON 进去看返回"，比说"用 SDK 的 inspector"更能证明你懂传输层。

---

## 阶段 2 · 换成你自己的数据（5 步，2–3 天）

**这是整个项目最重要的部分**。现在的 SQLite 是玩具，面试官一眼看穿。换成你学校里真实存在的东西，项目立刻从"教程复刻"变成"我的作品"。

### 步骤 8 · 选一个真实数据源

三选一，按可得性排序：

| 选项 | 难度 | 说明 |
|---|---|---|
| 学校图书馆检索页 / 教务公开课表 | 中 | 用 `requests` 抓一次，落成本地 SQLite 就行，不用做爬虫 |
| 你手上的 CSV / Excel（成绩、课表、社团名单） | 低 | 最快，但业务感弱 |
| 公开 API（GitHub / 天气 / 豆瓣图书） | 低 | 业务感弱，但稳定 |

**建议**：图书馆或课表。因为它天然有"读 + 写"两种操作，正好撑起你的权限模型。没有写操作的项目，权限和审批就是摆设。

### 步骤 9 · 建表并导入

在 `servers/` 下新建 `my_server.py`，抄 `campus_server.py` 的骨架，改三处：

```python
CONN = sqlite3.connect("data/mine.db", check_same_thread=False)  # ① 换成文件库
# ② 改 seed() 为你的建表语句
# ③ 改工具函数里的 SQL
```

导入脚本单独写一个 `scripts/import.py`，跑一次即可。

### 步骤 10 · 定义 5–7 个工具，必须含写操作

工具设计的硬要求：

- 至少 1 个**写工具**（改数据），否则权限模型无从体现
- 至少 1 个**敏感读工具**（涉及个人信息），否则审批门无从体现
- 每个工具的 `description` 写清"什么时候该用它"，不要写"查询课程"这种废话
- `inputSchema` 必须带 `additionalProperties: false` 和 `maxLength`

**验收**：`python -m servers.my_server` 之后不报错，`echo` 一条 `tools/list` 能看到你的工具。

### 步骤 11 · 注册进网关

改 `demo.py` 和 `bench.py` 顶部的 `SERVERS`：

```python
SERVERS = {
    "campus": ["-m", "servers.campus_server"],
    "mine":   ["-m", "servers.my_server"],     # 新增
}
```

再改 `POLICIES`：给你的写工具加上 principal 限制，改 `RISK_REGISTRY` 标出管理级工具。

### 步骤 12 · 跑通新的 demo

```bash
python demo.py
```

**预期**：工具总数变成 `9 + 你的数量`，guest 越权那条换成你的写工具名。

> 到这里，项目已经"是你的"了。后面三步是把它变成"能写简历的"。

---

## 阶段 3 · 接真实客户端（3 步，1 天）

自己写脚本调自己的 Server 不算数，**必须让别人的客户端能用**。

### 步骤 13 · 接 Claude Desktop（Windows）

打开 `%APPDATA%\Claude\claude_desktop_config.json`，加：

```json
{
  "mcpServers": {
    "campus": {
      "command": "python",
      "args": ["C:\\Users\\江00\\WorkBuddy\\2026-11-01-18-45-34\\project-01-mcp-gateway\\servers\\campus_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

完全退出 Claude Desktop（托盘也要退），再启动。对话框里会出现工具图标。

**验收**：在对话里说"帮我查一下 CS401 还有没有位置"，它能调你的工具并返回真实数据。**截图存下来，简历/README 要用。**

> 踩坑预警：如果客户端报 "server exited"，99% 是 Server 往 stdout 打了非 JSON 内容。检查你所有 `print()` —— 必须改成 `print(..., file=sys.stderr)`。

### 步骤 14 · 接 Cursor（可选，10 分钟）

`~/.cursor/mcp.json` 同结构。好处是能在写代码时直接调，演示效果更好。

### 步骤 15 · 接 LangGraph

```bash
pip install langgraph langchain-openai
```

```python
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import create_react_agent

gateway = Gateway(SERVERS, POLICIES, RISK_REGISTRY, cwd=ROOT).start()

def make_tool(desc):
    # OpenAI 的函数名只允许 [a-zA-Z0-9_-]，工具名里的 "." 必须换掉
    safe = desc["name"].replace(".", "__")
    def run(**kwargs):
        return gateway.call("admin", desc["name"], kwargs)["content"][0]["text"]
    return StructuredTool.from_function(func=run, name=safe, description=desc["description"])

agent = create_react_agent(model, [make_tool(t) for t in gateway.list_tools("admin")])
agent.invoke({"messages": [("user", "查一下我绩点，再看看 CS401 还能不能选")]})
```

**验收**：一个任务里连续调用了 2 个以上工具，**并且**中途触发了审批门（写操作被拦）。

> 这个"."的坑本身就是一个好谈资：MCP 允许工具名带点，OpenAI 的函数名不允许，网关必须做映射，否则接任何 LLM 都会 400。

---

## 阶段 4 · 把数字测出来（3 步，1 天）

README 里那些 `__` 就是这一步填的。

### 步骤 16 · 用真模型测工具选择准确率

```bash
export BENCH_API_KEY=你的key
export BENCH_BASE_URL=https://api.deepseek.com/v1
export BENCH_MODEL=deepseek-chat
python bench.py select --provider openai --out result.json
```

**预期**：`准确率：xx.x%  (n/24)`。

**再跑一次衰减曲线**（这是最值钱的一张图）：

```bash
for n in 4 6 8 9 12; do python bench.py select --provider openai --limit $n; done
```

把"工具数 → 准确率"记成表。工具越多准确率越低，这个结论本身就是你理解工程难点的证据。

> ⚠️ **`--provider mock` 的 91.7% 绝对不许写进简历**。那是关键词匹配器，不是模型。面试官让你当场复现就穿帮了。

### 步骤 17 · 测延迟

```bash
python bench.py latency --rounds 50
```

**预期**：`P50=15.x ms  P95=17.x ms`（本地 stdio，数值小是正常的，别吹成"高性能"）。

### 步骤 18 · 重跑红队，把拦截率写进 README

```bash
python bench.py injection
```

**要求**：必须 12/12。有 FAIL 就说明你的策略漏了，先修再算。

---

## 阶段 5 · 发布与简历（6 步，2 天）

### 步骤 19 · 建 GitHub 仓库

```bash
git init
git add .
git commit -m "feat: hand-written MCP server core (dual-era, no SDK)"
```

**关键：分 20+ 次提交，不要一次性 push。** 推荐提交节奏：

```
feat: protocol constants and newline framing
feat: server/discover with version negotiation
feat: tools/list deterministic ordering
feat: tools/call with isError semantics
feat: minimal JSON Schema validator
feat: legacy initialize fallback (dual-era)
feat: stdio client with request correlation
feat: gateway aggregation and namespacing
feat: risk-tiered authorization
feat: approval gate for write tools
feat: audit log with arg hashing
feat: retry with process restart
test: 16 cases across both protocol eras
bench: tool-selection and red-team harness
```

### 步骤 20 · README 补三节

必须有的三节，缺一节可信度掉一半：

1. **架构图**（用 draw.io 画，20 分钟）
2. **Difference from upstream**（README 里有模板，按你的实际改动改）
3. **Known limitations**（主动说只支持 stdio、没做 MRTR、风险表是静态的）

### 步骤 21 · 把真数字填进 README

把步骤 16–18 的结果替换掉所有 `__`。**填不出来的项就删掉那一句**，不要留空也不要编。

### 步骤 22 · 写技术博客

题目建议：**《MCP 在 2026 年 7 月去掉了会话，我照着新规范手写了一遍协议层》**

结构：旧握手怎么工作 → 为什么改 → 无状态带来的三个代价 → 我怎么实现双时代兼容 → 踩过的 3 个坑。

发在掘金/知乎/个人站都行。**这篇文章的传播价值比项目本身高**，也是面试时的"作品"。

### 步骤 23 · 给上游提一个 PR

最小可行：给 `modelcontextprotocol/servers` 补一个中文场景的 server，或给文档修一处错误。

**这一条的价值**：面试时一句"我的改动被官方仓库合并过"，比十句自夸有效。

### 步骤 24 · 更新简历

用 README 第 4 节那段模板，把数字换成真的。

**自查**：随便挑一句，你能不能讲 3 分钟？讲不了就删。

---

## 阶段 6 · 自测（3 步，面试前必做）

### 步骤 25 · 白板复现

关掉代码，在白纸上画出：`客户端 → 网关 → 三个 Server` 的消息流，标出 `_meta` 在哪一层加、`isError` 在哪一层被判。

画不出来 = 没吃透。

### 步骤 26 · 口述 12 问

README 第 5 节有 12 个问题。**录音回答**，每问控制在 90 秒。卡壳的那题就是你的漏洞。

### 步骤 27 · 现场演示准备

准备一条 40 秒的命令序列，能一次跑完：单测 → demo → 红队。面试官说"给我看看"，你 40 秒内出结果，比任何描述都强。

```bash
python -m unittest discover -s tests 2>&1 | tail -2 && python demo.py | tail -6 && python bench.py injection | tail -3
```

---

## 时间不够时的取舍

只有 2 周？按这个顺序砍：

1. **必做**：步骤 4–12（协议层 + 换真实数据）
2. **必做**：步骤 16（真模型准确率）—— 没数字等于没做
3. **必做**：步骤 19–20（GitHub + README）
4. 可砍：LangGraph 接入、博客、PR

反过来，**步骤 8（换真实数据）不能砍**。用我给的 SQLite 交作业，等于交了一份别人的作业。

---

## 现状：仓库已经补成成品（2026-11-01）

原来手册里"必须你自己补"的部分，现在大部分已经在代码里了：

| 原步骤 | 现状 |
|---|---|
| 步骤 8–12 真实数据 | ✅ 已做。`datasource/campus_db.py` 持久化 SQLite（10 门 AI 专业课程 / 3 名学生 / 100 条教室记录）；`datasource/github_api.py` 接**真实 GitHub REST API**，带磁盘缓存、ETag 304、限流降级 |
| 步骤 13–15 接客户端 | ✅ 已做。`clients/openai_tools.py`：`campus.search_courses` → `campus__search_courses` 映射（绕开 OpenAI 函数名限制），带审批中断的 Agent 循环，成本核算 |
| 步骤 16–18 测数字 | ✅ 部分。`bench.py` 五个子命令：`select` / `latency` / `e2e` / `net` / `injection`，实测数据已写进 README 第 3 节 |
| 步骤 19–24 发布 | ✅ 已做。Git 13 个提交（feat/test/bench/docs 分组），`.gitignore` 分清运行时产物，README 含 `Difference from upstream` |

**只剩三件事需要你本人做：**

1. **真模型的工具选择准确率**（README 里那两个 `__`）—— 需要你自己的 API Key，我这边没有。跑法见 README 第 3 节，DeepSeek 几毛钱。
2. **Claude Desktop / Cursor 的接入截图** —— 需要在你自己机器上装客户端、填 MCP 配置。这是"真实客户端用过"的唯一证据。
3. **给上游提一个 PR**（文档修正也算）—— 这是简历上最硬的一句话。

另外两件小事：把 `git config user.name/email` 改成你自己的（我填的是占位值 `Jiang`）；`git remote add origin <你的仓库>` 后推送。

