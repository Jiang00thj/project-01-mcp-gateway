# 项目一 · MCP 工具生态与工具网关

> 一句话：手写 MCP 协议层 + 构建多 Server 工具网关，把"工具供给"这件事从调 SDK 变成能讲清协议、能控权限、能审计的工程系统。

这个仓库是**骨架 + 手册**，不是成品。代码已经跑通（3 个 Server / 9 个 Tool / 16 个测试 / 12 例红队全过），但简历要用的数字、真实数据源、以及向上游的贡献，必须你自己补——那部分才是你的。

| 文件 | 用途 |
|---|---|
| **`STEPS.md`** | **27 步操作手册，含命令与预期输出，从这里开始** |
| `bench.py` | 测数字：工具选择准确率 / P95 延迟 / 红队拦截率 |
| `demo.py` | 五场景端到端演示 |

---

## 0. 先记住一个信息差

MCP 规范在 **2026-07-28** 做了一次大改：

| 改动 | 旧（≤2025-11-25） | 新（2026-07-28） |
|---|---|---|
| 会话 | `initialize` 握手 + `notifications/initialized` | **取消握手，完全无状态** |
| 版本协商 | 握手时谈一次 | **每个请求在 `_meta` 里自带版本** |
| 能力声明 | 握手时交换 | 每请求携带 `clientCapabilities` |
| 服务发现 | 无 | **必须实现 `server/discover`** |
| 结果结构 | 无统一标记 | 所有 result 必须带 `resultType` |

**市面上 90% 的教程还停在旧握手。** 面试时你能说出"7 月改版把会话去掉了，改成每请求带版本，所以我实现的是 dual-era：先用 `server/discover` 探测，收到现代错误才回退到 `initialize`"——这一句话就够面试官记住你。

本仓库的实现就是 dual-era（`mcp_min/server.py`），两条路径都有测试覆盖。

---

## 1. 三周日程（每周 25–30 小时）

### 第 1 周 · 协议层：把 SDK 拆开自己写一遍

| 天 | 任务 | 交付 |
|---|---|---|
| D1 | 通读 `specification/2026-07-28` 的 Overview / Versioning / stdio / Tools 四页，动手抄一遍消息流 | 一页笔记：请求-响应时序图 |
| D2 | 实现 `protocol.py`：错误码常量、`_meta` key、newline framing | 能 dump/parse 单条消息 |
| D3 | 实现 `server.py` 的 dispatch + 版本校验 + `server/discover` | `echo '{"jsonrpc":"2.0",...}' \| python -m servers.campus_server` 有响应 |
| D4 | 实现 `tools/list`（确定性排序）+ `tools/call`（content / structuredContent / isError） | 手测 3 个工具 |
| D5 | 实现 `validate.py` 最小 JSON Schema 校验，跑通"参数缺失返回 isError 而不是崩溃" | 单测通过 |
| D6 | **补 legacy 极**：`initialize` 分支 + 无 `_meta` 时的语义判定 | dual-era 两条路径都能跑 |
| D7 | 接真实数据源：把 `campus_server.py` 的 SQLite 换成你自己的（图书馆 API / 教务 / 本地知识库） | 5 个真实可用工具 |

**本周验收**

- [ ] `python -m unittest discover -s tests` 全绿
- [ ] 能手写一条 `tools/call` 的 JSON 并解释每个字段为什么存在
- [ ] 能回答：为什么日志必须写 stderr 而不是 stdout？（答案：stdout 是协议通道，一行脏输出整条流就废了）

### 第 2 周 · 网关：从"能用"到"敢给别人用"

| 天 | 任务 | 交付 |
|---|---|---|
| D8 | 写 `gateway/client.py`：subprocess 管理、请求 id 关联、超时、stderr 捕获 | 能驱动任意 server |
| D9 | 实现 `server/discover` 探测 + 多 server 聚合 + `server.tool` 命名空间 | 3 个 server 汇成一张工具表 |
| D10 | 实现风险分级 + principal 策略（allowlist / max_risk） | guest 越权被拒 |
| D11 | 实现审批门（write / sensitive → `ApprovalRequired`） | 敏感调用必须先拿到人确认 |
| D12 | 实现审计日志（jsonl，参数只存 hash 不存明文） | 每次调用都有记录 |
| D13 | 实现重试 + 进程重启 + 结果截断（保头保尾） | 杀掉 server 进程后网关能自愈 |
| D14 | 实现调用预算（防 Agent 死循环烧钱） | 第 N 次调用被拦并记审计 |

**本周验收**

- [ ] `python demo.py` 五个场景全部通过断言
- [ ] 能解释：为什么风险表要放在网关侧，而不是信任 Server 的 `annotations`？（规范原文：annotations 视为不可信）
- [ ] 能解释：截断为什么保留尾部？（尾部常是结论/报错，全砍会让模型自信地编）

### 第 3 周 · 落地 + 量化 + 开源化

| 天 | 任务 | 交付 |
|---|---|---|
| D15 | 接 **Claude Desktop / Cursor**：把 `servers/campus_server.py` 写进它的 MCP 配置，真正在对话里用起来 | 一张截图：模型成功调用你的工具 |
| D16 | 接 **LangGraph**：把网关暴露成 `@tool`，做一次"查课 → 算绩点 → 判断保研资格"的多步任务 | 一段可跑通的 Agent 流程 |
| D17 | 埋点测指标：工具选择准确率、P95 延迟、注入拦截率 | 一张指标表 |
| D18 | **红队测试**：构造 20 条恶意输入（工具描述投毒、参数注入 `'; DROP TABLE`、超大返回撑爆上下文） | 拦截率数字 |
| D19 | 写 README + 架构图 + `Difference from upstream` 章节 | 仓库可公开 |
| D20 | 写技术博客：为什么 MCP 要去掉会话 | 一篇 3000 字 |
| D21 | 给上游提 PR（文档修正 / 新增一个社区 server / 补一个测试） | PR 链接 |

**本周验收**

- [ ] 有真实客户端截图（不是自己写脚本自娱自乐）
- [ ] 指标表至少 4 个数字
- [ ] 博客已发布，链接可访问

---

## 2. 指标怎么测（面试官只认这些）

| 指标 | 测法 | 目标 |
|---|---|---|
| 工具选择准确率 | 构造 30 条自然语言指令 → 看模型选对 tool 的比例（先 9 个工具，再加到 20 个看衰减） | ≥ 90%，并给出"工具数 vs 准确率"曲线 |
| P95 调用延迟 | 单工具 200 次调用，取 P95 | < 300ms（本地） |
| 注入拦截率 | 20 条恶意用例（参数注入 / 描述投毒 / 路径穿越） | 100% 拦截，误杀率 < 5% |
| 越权拦截 | 3 类 principal × 9 个工具 = 27 组，预期拒绝数应精确匹配 | 100% |
| 成本 | 每次任务 token 数 + 工具返回字符数 | 给出截断前后的对比 |

**"工具数 vs 准确率"这条曲线是全场最值钱的**——它证明你理解"工具多了模型会选错"这个真实工程问题，而不是堆功能。

---

## 3. Difference from upstream（直接写进 README）

> 本项目的协议层参考 MCP 规范 `2026-07-28` 手写实现，未使用官方 SDK（`modelcontextprotocol/python-sdk`）。差异如下：
>
> 1. **双时代支持**：官方 SDK 面向单一协议版本；本实现用 `server/discover` 探测 + 错误码回退，同时服务现代客户端与 legacy `initialize` 客户端。
> 2. **网关层为原创**：多 Server 聚合、命名空间消歧、风险分级、审批门、审计、预算控制均无上游对应实现。
> 3. **风险表独立于服务端声明**：依据规范"annotations 不可信"的要求，风险等级由网关侧注册表决定。
> 4. **截断策略**：保留头尾并注入 `[truncated N chars]` 标记，避免模型误判文件已被完整读取。
> 5. **简化项**：未实现 `subscriptions/listen`、MRTR（`input_required`）、Streamable HTTP 传输与 OAuth；仅覆盖 stdio。

**这一节的作用**：面试官点进仓库 30 秒内就知道哪些是你的。主动划界 = 可信度。

---

## 4. 简历怎么写

> **MCP 工具网关｜个人项目｜2026.01–2026.02**
> 基于 MCP 协议（2026-07-28 修订版）手写 JSON-RPC 协议层，实现同时兼容无状态新版本与 legacy 握手客户端的 dual-era Server；构建多 Server 工具网关，支持 3 类 Server / 9 个 Tool 的动态发现与 `server.tool` 命名空间聚合，实现基于 principal 的三级权限模型与写操作审批门，累计拦截越权调用 __ 次；设计保留头尾的结果截断与调用预算机制，将单任务工具返回 token 降低 __%，死循环导致的无效调用归零；接入 Claude Desktop 与 LangGraph 双客户端，工具选择准确率 __%（9 工具）/ __%（20 工具）。

注意：**每个 `__` 都必须是真实测出来的**。填不出来就先别写这个项目。

---

## 5. 面试必答（按被问频率排序）

1. MCP 和 function calling 的区别是什么？为什么需要协议？
2. 为什么 2026-07 的改版要去掉 `initialize` 会话？无状态带来什么代价？
3. stdio 传输为什么要求一行一条、且不能有内嵌换行？
4. 你的网关怎么处理两个 Server 同名工具？
5. 工具调用失败了，你怎么让模型自己纠正而不是整个流程崩掉？
6. `isError: true` 和 JSON-RPC `error` 的区别？什么情况用哪个？
7. 为什么不信 Server 的 `annotations`？攻击者能怎么利用？
8. 工具从 9 个涨到 50 个，准确率会怎么变？你怎么缓解？
9. 结果太长怎么办？只截前半段有什么风险？
10. 怎么防 Agent 反复调同一个工具？
11. 审计日志为什么只存参数 hash？
12. 如果要做 HTTP 传输，你需要改哪些地方？

---

## 6. 已知局限（写进 README，面试时主动说）

- 只实现 stdio，未做 Streamable HTTP 与鉴权
- 未实现 `subscriptions/listen` 与 MRTR，长任务只能靠超时兜底
- 风险表是静态配置，未做动态学习
- 单进程串行调用，未做并发调度

**主动说局限的人，比被问出来的人高一档。**

---

## 7. 运行

```bash
python -m unittest discover -s tests -v   # 16 个测试
python demo.py                            # 五场景端到端演示
python bench.py injection                 # 红队 12 例
python bench.py select --provider mock    # 管道自检（mock 数字不能进简历）
python bench.py select --provider openai  # 真模型，需 BENCH_API_KEY
python bench.py latency --rounds 50       # P50 / P95
```

零第三方依赖，Python 3.10+。真实接入客户端时再装官方 SDK 做对照实验。

## 8. 目录

```
STEPS.md       27 步操作手册（先看这个）
mcp_min/       手写协议层：protocol / server / validate
servers/       三个示例 Server：campus / library / notice
gateway/       client（stdio 驱动）+ gateway（聚合·权限·审计）
bench/         基准数据与 LLM 适配
bench.py       指标测量入口
tests/         16 个单测，覆盖双时代协议与策略
demo.py        五场景演示
```
