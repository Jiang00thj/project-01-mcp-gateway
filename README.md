# 项目一 · MCP 工具网关（Tool Gateway）

手写 MCP 协议层 + 多 Server 工具网关：把"给 Agent 供工具"从"调个 SDK"做成能讲清协议、能控权限、能审计、能量化的工程系统。

**当前状态：可运行、可测量、可提交。** 4 个 Server / 12 个 Tool / 45 个单测 / 12 例红队全过 / 真实外部 API 带缓存。零第三方依赖。

```bash
python demo.py                            # 7 个场景，全部断言
python -m unittest discover -s tests      # 45 个单测，离线可跑
python bench.py all --out result.json     # 出数字
```

---

## 1. 一个信息差（面试时最好用的一句）

MCP 规范在 **2026-07-28** 做了大改：

| 改动 | 旧（≤ 2025-11-25） | 新（2026-07-28） |
|---|---|---|
| 会话 | `initialize` + `notifications/initialized` | **取消握手，完全无状态** |
| 版本协商 | 握手时谈一次 | **每个请求在 `_meta` 里自带版本** |
| 服务发现 | 无 | **必须实现 `server/discover`** |
| 结果结构 | 无统一标记 | 所有 result 必须带 `resultType` |
| 未知版本 | `-32602` | 专用错误码 **`-32022`** |

本实现是 **dual-era**：先用 `server/discover` 探测现代服务端，收到不支持的信号再回退到 `initialize`。两条路径都有单测（`mcp_min/server.py`）。

市面上绝大多数教程还停在旧握手。这个差异只要说出"7 月改版把会话去掉了"，就足以区分"照着教程抄"和"读过规范"。

---

## 2. 架构

```
                     ┌──────────────────────────────────────────┐
   模型 / LangGraph → │  clients/  名字映射 · 轨迹 · 审批回调      │
                     └──────────────────┬───────────────────────┘
                                        │ OpenAI function schema
                     ┌──────────────────▼───────────────────────┐
                     │  gateway/  聚合 · 命名空间 · 风险分级      │
                     │            审批门 · 审计 · 预算 · 截断     │
                     └───┬──────────┬──────────┬──────────┬─────┘
                    stdio│     stdio│     stdio│     stdio│
                  ┌──────▼──┐ ┌─────▼────┐ ┌───▼─────┐ ┌──▼──────┐
                  │ campus  │ │ library  │ │ notice  │ │ github  │
                  │ SQLite  │ │ SQLite   │ │ SQLite  │ │ REST API│
                  └─────────┘ └──────────┘ └─────────┘ └────┬────┘
                                                            │缓存/ETag/降级
                                                     datasource/github_api.py
```

- **`mcp_min/`** 协议层：JSON-RPC framing、双时代 dispatch、最小 JSON Schema 校验。零依赖。
- **`datasource/`** 数据层：SQLite 校园库 + 真实 GitHub REST 客户端（磁盘缓存、ETag、限流降级）。
- **`servers/`** 4 个 MCP Server，共 12 个 Tool，含读写两类。
- **`gateway/`** 核心原创部分：策略、审批、审计、预算、截断、进程自愈。
- **`clients/`** 接入层：`. → __` 名字映射、带审批中断的 Agent 循环、成本核算。

---

## 3. 实测数据（`bench.py all`，2026-11-01）

| 指标 | 结果 | 怎么测的 |
|---|---|---|
| Server / Tool 数 | 4 / 12 | `demo.py` 场景 1 |
| 单测 | **45 通过** | `unittest discover` |
| 红队拦截率 | **100%（12/12）** | 参数注入 / 描述投毒 / 伪造版本 / 预算 / 截断 |
| 网关延迟 | **P50 16.0ms，P95 18.7ms** | 30 次 `tools/call`，本地 stdio，含鉴权+审计落盘 |
| 端到端轨迹 | **4/4 通过**，7 次工具调用，平均 33.2ms | scripted 模型，覆盖 允许/拒绝/审批 三条分支 |
| 外网缓存 | 冷 **735.8ms** → 命中 **10.8ms**，**68×** | GitHub `search/issues`，冷=ttl 0，热=ttl 24h |
| 工具选择准确率 | 78.6%（mock，**不可入简历**） | 关键词打分器，只证明管道通 |

最后一行必须说明白：那是 mock 基线，不是模型能力。真数字要 `--provider openai`（DeepSeek 即可，28 条用例几毛钱）：

```bash
export BENCH_API_KEY=sk-xxx
export BENCH_BASE_URL=https://api.deepseek.com/v1
export BENCH_MODEL=deepseek-chat
python bench.py select --provider openai --limit 12 --out select12.json
python bench.py select --provider openai --limit 6  --out select6.json   # 画衰减曲线
```

---

## 4. 五个能撑住追问的设计决策

**① 风险表放在网关，不信 Server 的 `annotations`**
规范原文写明 annotations 不可信。红队用例 `poisoned_annotation` 让 Server 谎称写工具是只读——网关按自己的注册表判定为 `admin`，直接拒。这是"工具投毒"最直接的防线。

**② 截断保留头尾，并显式标注**
只砍后半段会让模型以为自己读到了完整内容，然后自信地编。测试与演示都在验证这一点。
**连带发现**：把 JSON 文本截断后它就不可解析了，所以需要机器可读结果的地方必须读 `structuredContent`（协议里专门为此存在）。`demo.py` 场景 4 就演示了这个坑。

**③ 审批是"暂停"，不是"失败"**
`ApprovalRequired` 在循环里被 `on_approval` 回调接住，用户同意后**带 `approved=True` 重放同一调用**，轨迹不断裂；拒绝则回一条"换种做法"的工具消息给模型，不终止任务。

**④ 外网工具必须能降级**
无鉴权 GitHub 每小时 60 次。天真实现会在 403 时抛错，把整条轨迹拖死。这里：磁盘缓存 + ETag 304 + 限流时返回 `stale` 缓存并把降级原因写进 `_meta`，模型能看到"这是旧数据"而不是"失败了"。

**⑤ 看不到的工具也走鉴权**
模型只能看到 `list_tools` 返回的工具，但循环里对"未知名字"仍做 `__ → .` 还原后走一遍 `_authorize`。多一层判断，避免幻觉工具名绕过策略层。

---

## 5. Difference from upstream

协议层参考 MCP 规范 `2026-07-28` 手写，**未使用**官方 SDK（`modelcontextprotocol/python-sdk`）或 FastMCP：

1. **双时代支持**：官方 SDK 面向单一协议版本；本实现用 `server/discover` + 错误码回退，同时服务无状态新客户端与 legacy `initialize` 客户端。
2. **网关层为原创**：多 Server 聚合、命名空间消歧、风险分级、审批门、审计、预算、进程自愈，上游均无对应实现。
3. **风险表独立于服务端声明**：依据规范"annotations 不可信"的要求。
4. **截断策略**：保头保尾 + 显式标记，而非丢弃尾部。
5. **缓存与降级**：`datasource/github_api.py` 自行实现 ETag/304、限流兜底、离线 stale，未引入 `requests`/`httpx`。
6. **简化项**：未实现 `subscriptions/listen`、MRTR（`input_required`）、Streamable HTTP 传输与 OAuth；仅覆盖 stdio。

---

## 6. 简历写法（数字都可追溯）

> **MCP 工具网关｜个人项目｜2026.11**
> 依据 MCP 规范 2026-07-28 修订版手写 JSON-RPC 协议层（零第三方依赖），实现兼容无状态新版与 legacy 握手客户端的 dual-era Server；构建聚合 4 个 Server / 12 个 Tool 的工具网关，实现 `server.tool` 命名空间消歧、基于 principal 的三级权限模型与写操作审批门；为真实外部 API 实现磁盘缓存 + ETag 条件请求 + 限流降级，冷启动 735.8ms → 命中 10.8ms（68×）；结果截断保留头尾并显式标注，死循环由调用预算拦截；45 个单测、12 例红队攻击面拦截率 100%，网关 P95 延迟 18.7ms。

还差两个数字（只有真模型能测）：工具选择准确率 `__%`（12 工具）/ `__%`（6 工具）。

---

## 7. 面试必答

1. MCP 和 function calling 的区别？为什么需要协议层？
2. 2026-07 改版为什么去掉 `initialize` 会话？无状态的代价是什么？
3. stdio 传输为什么必须一行一条、且日志只能走 stderr？
4. 两个 Server 有同名工具怎么办？（命名空间 + 消歧规则）
5. 工具失败怎么让模型自己纠正而不崩流程？（`isError` vs JSON-RPC `error` 的边界）
6. 为什么不信 Server 的 `annotations`？攻击者怎么利用？
7. 工具从 12 个涨到 50 个会怎样？怎么缓解？（分层暴露 / 检索式工具选择）
8. 结果太长怎么办？只截前半段的风险是什么？
9. 怎么防 Agent 反复调同一个工具？（预算 + 去重 + 步数）
10. 审计日志为什么只存参数 hash？
11. 外部 API 限流了，你的工具会怎么表现？
12. 要做 HTTP 传输，需要改哪些地方？

---

## 8. 已知局限（主动说）

- 只实现 stdio，未做 Streamable HTTP 与鉴权
- 未实现 `subscriptions/listen` 与 MRTR，长任务靠超时兜底
- 风险表是静态配置，未做动态学习
- 单进程串行调用，未做并发调度
- 工具选择基准的 mock provider 只是关键词打分，不是模型

---

## 9. 目录

```
mcp_min/        协议层：protocol（framing/错误码）/ server（双时代）/ validate
datasource/     campus_db（SQLite）/ github_api（缓存·ETag·降级）
servers/        campus · library · notice · github（共 12 tool）
gateway/        client（stdio 驱动）/ gateway（聚合·策略·审计·预算·截断）
clients/        openai_tools（名字映射·Agent 循环·成本）
benchkit/       基准用例与模型适配
bench.py        指标入口：select / latency / e2e / net / injection
demo.py         7 场景演示（含真实 API 与 LLM 循环）
tests/          45 个单测：协议 / 策略 / 缓存 / 校验 / 循环
STEPS.md        27 步操作手册
```

## 10. 环境

Python 3.10+，零第三方依赖。可选环境变量：

| 变量 | 作用 |
|---|---|
| `GITHUB_TOKEN` | 提高 GitHub 限流额度（60/h → 5000/h） |
| `GITHUB_WRITE=1` | 允许 `comment_issue` 真正写入（默认 dry-run） |
| `BENCH_API_KEY` / `BENCH_BASE_URL` / `BENCH_MODEL` | 测真模型的工具选择准确率 |
| `CAMPUS_DB` | 指定另一个 SQLite 路径（测试用） |
